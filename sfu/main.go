package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
)

// Room holds a publisher track and subscribers.
type Room struct {
	mu          sync.RWMutex
	videoTrack  *webrtc.TrackLocalStaticRTP
	publisher   *webrtc.PeerConnection
	subscribers []*webrtc.PeerConnection
}

var (
	rooms   = map[string]*Room{}
	roomsMu sync.RWMutex
	api     *webrtc.API
)

func getRoom(name string) *Room {
	roomsMu.Lock()
	defer roomsMu.Unlock()
	r, ok := rooms[name]
	if !ok {
		r = &Room{}
		rooms[name] = r
	}
	return r
}

func main() {
	natIP := os.Getenv("NAT_IP")
	if natIP == "" {
		natIP = "127.0.0.1"
	}
	udpPort := 59055
	if p, err := strconv.Atoi(os.Getenv("UDP_PORT")); err == nil {
		udpPort = p
	}
	httpPort := 8088
	if p, err := strconv.Atoi(os.Getenv("HTTP_PORT")); err == nil {
		httpPort = p
	}

	log.Printf("SFU starting: NAT_IP=%s UDP_PORT=%d HTTP_PORT=%d", natIP, udpPort, httpPort)

	// --- pion SettingEngine ---
	se := webrtc.SettingEngine{}
	se.SetLite(true)
	se.SetNAT1To1IPs([]string{natIP}, webrtc.ICECandidateTypeHost)

	// Single-port UDP mux
	udpListener, err := net.ListenPacket("udp4", fmt.Sprintf("0.0.0.0:%d", udpPort))
	if err != nil {
		log.Fatalf("Failed to listen UDP: %v", err)
	}
	mux := ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: udpListener})
	se.SetICEUDPMux(mux)

	se.SetNetworkTypes([]webrtc.NetworkType{
		webrtc.NetworkTypeUDP4,
	})

	api = webrtc.NewAPI(webrtc.WithSettingEngine(se))

	log.Printf("ICE Lite enabled, NAT 1:1 → %s, UDP+TCP mux on :%d", natIP, udpPort)

	// --- HTTP signaling ---
	http.HandleFunc("/offer", handleOffer)
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("ok"))
	})

	addr := fmt.Sprintf("0.0.0.0:%d", httpPort)
	log.Printf("HTTP signaling on %s", addr)
	log.Fatal(http.ListenAndServe(addr, nil))
}

type offerRequest struct {
	SDP string `json:"sdp"`
}

type answerResponse struct {
	SDP  string `json:"sdp"`
	Type string `json:"type"`
}

func handleOffer(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}

	roomName := r.URL.Query().Get("room")
	role := r.URL.Query().Get("role") // "pub" or "sub"
	if roomName == "" || (role != "pub" && role != "sub") {
		http.Error(w, "need ?room=xxx&role=pub|sub", http.StatusBadRequest)
		return
	}

	var req offerRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}

	offer := webrtc.SessionDescription{
		Type: webrtc.SDPTypeOffer,
		SDP:  req.SDP,
	}

	room := getRoom(roomName)

	var answer *webrtc.SessionDescription
	var err error

	if role == "pub" {
		answer, err = handlePublisher(room, offer)
	} else {
		answer, err = handleSubscriber(room, offer)
	}

	if err != nil {
		log.Printf("[%s/%s] error: %v", roomName, role, err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(answerResponse{
		SDP:  answer.SDP,
		Type: answer.Type.String(),
	})
}

func handlePublisher(room *Room, offer webrtc.SessionDescription) (*webrtc.SessionDescription, error) {
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, fmt.Errorf("new pc: %w", err)
	}

	// Always create a fresh track to avoid RTP sequence discontinuity
	videoTrack, vtErr := webrtc.NewTrackLocalStaticRTP(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8},
		"video", "tabshare",
	)
	if vtErr != nil {
		return nil, fmt.Errorf("new track: %w", vtErr)
	}

	room.mu.Lock()
	room.videoTrack = videoTrack
	oldPC := room.publisher
	room.publisher = pc
	// Close all existing subscribers (they'll auto-reconnect with clean decoder)
	oldSubs := room.subscribers
	room.subscribers = nil
	room.mu.Unlock()

	if oldPC != nil {
		log.Printf("[pub] closing old publisher connection")
		oldPC.Close()
	}
	for _, sub := range oldSubs {
		log.Printf("[pub] closing subscriber for clean reconnect")
		sub.Close()
	}

	pc.OnTrack(func(remoteTrack *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		log.Printf("[pub] got track: %s codec=%s", remoteTrack.ID(), remoteTrack.Codec().MimeType)

		// Send periodic PLI to ensure regular keyframes (compensates for no NACK/jitter buffer)
		go func() {
			ticker := time.NewTicker(2 * time.Second)
			defer ticker.Stop()
			// Immediate first PLI
			pc.WriteRTCP([]rtcp.Packet{
				&rtcp.PictureLossIndication{MediaSSRC: uint32(remoteTrack.SSRC())},
			})
			for range ticker.C {
				if pc.ConnectionState() != webrtc.PeerConnectionStateConnected {
					return
				}
				pc.WriteRTCP([]rtcp.Packet{
					&rtcp.PictureLossIndication{MediaSSRC: uint32(remoteTrack.SSRC())},
				})
			}
		}()

		// Read RTP from publisher and write to persistent local track (broadcast)
		buf := make([]byte, 4096)
		for {
			n, _, readErr := remoteTrack.Read(buf)
			if readErr != nil {
				log.Printf("[pub] track read ended: %v", readErr)
				return
			}
			if _, writeErr := videoTrack.Write(buf[:n]); writeErr != nil {
				log.Printf("[pub] track write error: %v", writeErr)
				return
			}
		}
	})

	pc.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		log.Printf("[pub] ICE state: %s", state.String())
		if state == webrtc.ICEConnectionStateFailed || state == webrtc.ICEConnectionStateDisconnected {
			pc.Close()
		}
	})

	if err = pc.SetRemoteDescription(offer); err != nil {
		return nil, fmt.Errorf("set remote: %w", err)
	}

	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return nil, fmt.Errorf("create answer: %w", err)
	}

	gatherComplete := webrtc.GatheringCompletePromise(pc)
	if err = pc.SetLocalDescription(answer); err != nil {
		return nil, fmt.Errorf("set local: %w", err)
	}
	<-gatherComplete

	localDesc := pc.LocalDescription()
	log.Printf("[pub] answer ready, ICE candidates gathered")
	return localDesc, nil
}

func handleSubscriber(room *Room, offer webrtc.SessionDescription) (*webrtc.SessionDescription, error) {
	room.mu.RLock()
	track := room.videoTrack
	pubPC := room.publisher
	room.mu.RUnlock()

	if track == nil {
		return nil, fmt.Errorf("no publisher in room yet")
	}

	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, fmt.Errorf("new pc: %w", err)
	}

	rtpSender, err := pc.AddTrack(track)
	if err != nil {
		return nil, fmt.Errorf("add track: %w", err)
	}

	// Read RTCP (PLI, NACK etc.) from viewer and forward PLI to publisher
	go func() {
		buf := make([]byte, 1500)
		for {
			if _, _, rtcpErr := rtpSender.Read(buf); rtcpErr != nil {
				return
			}
		}
	}()

	pc.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		log.Printf("[sub] ICE state: %s", state.String())
		if state == webrtc.ICEConnectionStateConnected {
			// Send PLI to publisher to get a keyframe for this new viewer
			if pubPC != nil {
				for _, receiver := range pubPC.GetReceivers() {
					if receiver.Track() != nil {
						pubPC.WriteRTCP([]rtcp.Packet{
							&rtcp.PictureLossIndication{
								MediaSSRC: uint32(receiver.Track().SSRC()),
							},
						})
						log.Printf("[sub] sent PLI to publisher for keyframe")
					}
				}
			}
		}
		if state == webrtc.ICEConnectionStateFailed || state == webrtc.ICEConnectionStateDisconnected {
			pc.Close()
			room.mu.Lock()
			for i, s := range room.subscribers {
				if s == pc {
					room.subscribers = append(room.subscribers[:i], room.subscribers[i+1:]...)
					break
				}
			}
			room.mu.Unlock()
		}
	})

	if err = pc.SetRemoteDescription(offer); err != nil {
		return nil, fmt.Errorf("set remote: %w", err)
	}

	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return nil, fmt.Errorf("create answer: %w", err)
	}

	gatherComplete := webrtc.GatheringCompletePromise(pc)
	if err = pc.SetLocalDescription(answer); err != nil {
		return nil, fmt.Errorf("set local: %w", err)
	}
	<-gatherComplete

	room.mu.Lock()
	room.subscribers = append(room.subscribers, pc)
	room.mu.Unlock()

	localDesc := pc.LocalDescription()
	log.Printf("[sub] answer ready, ICE candidates gathered")
	return localDesc, nil
}
