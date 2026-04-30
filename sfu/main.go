package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// Room holds a publisher track and subscribers.
type Room struct {
	mu          sync.RWMutex
	videoTrack  *webrtc.TrackLocalStaticRTP
	publisher   *webrtc.PeerConnection
	subscribers map[string]*Subscriber // id -> subscriber
	recorder    *Recorder              // WebM recorder (nil = not recording)
	// RTP sequence/timestamp continuity across publisher reconnects
	seqOffset uint16
	tsOffset  uint32
	lastSeq   uint16
	lastTS    uint32
	seqReset  bool
}

// Subscriber holds a subscriber's connection and track sender.
type Subscriber struct {
	pc     *webrtc.PeerConnection
	sender *webrtc.RTPSender
	room   string // current room name
}

var (
	rooms   = map[string]*Room{}
	roomsMu sync.RWMutex
	api     *webrtc.API
	// Global subscriber registry for cross-room switching
	allSubs   = map[string]*Subscriber{}
	allSubsMu sync.RWMutex
	subIDCounter uint64
	subIDMu      sync.Mutex
)

func getRoom(name string) *Room {
	roomsMu.Lock()
	defer roomsMu.Unlock()
	r, ok := rooms[name]
	if !ok {
		r = &Room{subscribers: map[string]*Subscriber{}}
		rooms[name] = r
	}
	return r
}

func nextSubID() string {
	subIDMu.Lock()
	defer subIDMu.Unlock()
	subIDCounter++
	return fmt.Sprintf("sub_%d", subIDCounter)
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

	// --- MediaEngine: register H.264 + VP9 + AV1 ---
	// Publisher SDP munging controls which codec is actually used.
	// SFU just needs to support all options for flexibility.
	me := &webrtc.MediaEngine{}
	for _, codec := range []webrtc.RTPCodecParameters{
		// H.264 High Profile (preferred for VAAPI)
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:    webrtc.MimeTypeH264,
				ClockRate:   90000,
				SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640032",
			},
			PayloadType: 123,
		},
		// H.264 Constrained Baseline (fallback)
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:    webrtc.MimeTypeH264,
				ClockRate:   90000,
				SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
			},
			PayloadType: 125,
		},
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:    webrtc.MimeTypeH264,
				ClockRate:   90000,
				SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f",
			},
			PayloadType: 102,
		},
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:    webrtc.MimeTypeH264,
				ClockRate:   90000,
				SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=0;profile-level-id=42001f",
			},
			PayloadType: 127,
		},
		// VP9
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:  webrtc.MimeTypeVP9,
				ClockRate: 90000,
			},
			PayloadType: 98,
		},
		// AV1
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:  webrtc.MimeTypeAV1,
				ClockRate: 90000,
			},
			PayloadType: 35,
		},
	} {
		if err := me.RegisterCodec(codec, webrtc.RTPCodecTypeVideo); err != nil {
			log.Fatalf("Failed to register codec: %v", err)
		}
	}
	log.Printf("MediaEngine: H.264 + VP9 + AV1 registered (publisher SDP controls selection)")

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

	api = webrtc.NewAPI(
		webrtc.WithMediaEngine(me),
		webrtc.WithSettingEngine(se),
	)

	log.Printf("ICE Lite enabled, NAT 1:1 → %s, UDP+TCP mux on :%d", natIP, udpPort)

	// --- HTTP signaling ---
	http.HandleFunc("/offer", handleOffer)
	http.HandleFunc("/switch", handleSwitch)
	http.HandleFunc("/pli", handlePLI)
	http.HandleFunc("/recording", handleRecording)
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
	SDP   string `json:"sdp"`
	Type  string `json:"type"`
	SubID string `json:"sub_id,omitempty"`
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
	var subID string

	if role == "pub" {
		answer, err = handlePublisher(room, offer)
	} else {
		answer, subID, err = handleSubscriber(room, roomName, offer)
	}

	if err != nil {
		log.Printf("[%s/%s] error: %v", roomName, role, err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(answerResponse{
		SDP:   answer.SDP,
		Type:  answer.Type.String(),
		SubID: subID,
	})
}

func handlePublisher(room *Room, offer webrtc.SessionDescription) (*webrtc.SessionDescription, error) {
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, fmt.Errorf("new pc: %w", err)
	}

	// Detect preferred codec from offer SDP
	preferredMime := webrtc.MimeTypeH264 // default
	if strings.Contains(offer.SDP, "VP9/90000") && !strings.Contains(offer.SDP, "H264/90000") {
		preferredMime = webrtc.MimeTypeVP9
	} else if strings.Contains(offer.SDP, "AV1/90000") && !strings.Contains(offer.SDP, "H264/90000") && !strings.Contains(offer.SDP, "VP9/90000") {
		preferredMime = webrtc.MimeTypeAV1
	}

	// Create or reuse track (subscribers stay connected)
	room.mu.Lock()
	if room.videoTrack == nil || room.videoTrack.Codec().MimeType != preferredMime {
		vt, vtErr := webrtc.NewTrackLocalStaticRTP(
			webrtc.RTPCodecCapability{MimeType: preferredMime},
			"video", "tabshare",
		)
		if vtErr != nil {
			room.mu.Unlock()
			return nil, fmt.Errorf("new track: %w", vtErr)
		}
		room.videoTrack = vt
		log.Printf("[pub] created local track: codec=%s", preferredMime)
	}
	oldPC := room.publisher
	room.publisher = pc
	// Signal sequence reset for next RTP packet
	room.seqReset = true
	room.mu.Unlock()

	if oldPC != nil {
		log.Printf("[pub] closing old publisher connection")
		oldPC.Close()
	}

	pc.OnTrack(func(remoteTrack *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		codecMime := remoteTrack.Codec().MimeType
		log.Printf("[pub] got track: %s codec=%s", remoteTrack.ID(), codecMime)

		// Update track if actual codec differs from what we predicted
		room.mu.Lock()
		if room.videoTrack.Codec().MimeType != codecMime {
			vt, vtErr := webrtc.NewTrackLocalStaticRTP(
				webrtc.RTPCodecCapability{MimeType: codecMime},
				"video", "tabshare",
			)
			if vtErr != nil {
				room.mu.Unlock()
				log.Printf("[pub] failed to recreate track for %s: %v", codecMime, vtErr)
				return
			}
			room.videoTrack = vt
			log.Printf("[pub] updated local track: codec=%s", codecMime)
		}
		videoTrack := room.videoTrack
		room.mu.Unlock()

		// Send a single PLI at start for quick first-frame, no periodic PLI
		// (periodic PLI causes stuttering from oversized I-frames)
		go func() {
			pc.WriteRTCP([]rtcp.Packet{
				&rtcp.PictureLossIndication{MediaSSRC: uint32(remoteTrack.SSRC())},
			})
		}()

		// Read RTP, rewrite seq/ts for continuity, forward to local track
		buf := make([]byte, 65535)
		var pktCount, kfCount uint64
		for {
			n, _, readErr := remoteTrack.Read(buf)
			if readErr != nil {
				log.Printf("[pub] track read ended: %v", readErr)
				return
			}

			// Parse, rewrite, write
			pkt := &rtp.Packet{}
			if err := pkt.Unmarshal(buf[:n]); err != nil {
				continue
			}

			room.mu.Lock()
			if room.seqReset {
				// Calculate offsets to maintain continuity
				room.seqOffset = room.lastSeq - pkt.Header.SequenceNumber + 1
				room.tsOffset = room.lastTS - pkt.Header.Timestamp + 1
				room.seqReset = false
				log.Printf("[pub] RTP rewriter reset: seqOffset=%d tsOffset=%d", room.seqOffset, room.tsOffset)
			}
			pkt.Header.SequenceNumber += room.seqOffset
			pkt.Header.Timestamp += room.tsOffset
			room.lastSeq = pkt.Header.SequenceNumber
			room.lastTS = pkt.Header.Timestamp
			room.mu.Unlock()

			// Tee to recorder (clone to avoid buf reuse corruption)
			room.mu.RLock()
			rec := room.recorder
			room.mu.RUnlock()
			if rec != nil {
				clonedPkt := &rtp.Packet{
					Header:  pkt.Header,
					Payload: make([]byte, len(pkt.Payload)),
				}
				copy(clonedPkt.Payload, pkt.Payload)
				rec.PushRTP(clonedPkt)
			}

			// H.264 keyframe detection: NAL type 5 = IDR frame
			pktCount++
			if len(pkt.Payload) > 0 {
				nalType := pkt.Payload[0] & 0x1F
				if nalType == 5 { // IDR = keyframe
					kfCount++
				} else if nalType == 24 && len(pkt.Payload) > 3 {
					// STAP-A: check first NAL unit inside
					innerNalType := pkt.Payload[3] & 0x1F
					if innerNalType == 5 {
						kfCount++
					}
				} else if nalType == 28 && len(pkt.Payload) > 1 {
					// FU-A: check start bit + NAL type
					fuHeader := pkt.Payload[1]
					isStart := (fuHeader & 0x80) != 0
					fuNalType := fuHeader & 0x1F
					if isStart && fuNalType == 5 {
						kfCount++
					}
				}
			}
			if pktCount%300 == 0 {
				log.Printf("[pub] RTP stats: %d pkts, %d keyframes (%.1f%%)", pktCount, kfCount, float64(kfCount)/float64(pktCount)*100)
			}

			if writeErr := videoTrack.WriteRTP(pkt); writeErr != nil {
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

func handleSubscriber(room *Room, roomName string, offer webrtc.SessionDescription) (*webrtc.SessionDescription, string, error) {
	room.mu.RLock()
	track := room.videoTrack
	room.mu.RUnlock()

	if track == nil {
		return nil, "", fmt.Errorf("no publisher in room yet")
	}

	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, "", fmt.Errorf("new pc: %w", err)
	}

	rtpSender, err := pc.AddTrack(track)
	if err != nil {
		return nil, "", fmt.Errorf("add track: %w", err)
	}

	subID := nextSubID()
	sub := &Subscriber{pc: pc, sender: rtpSender, room: roomName}

	// Read RTCP (PLI, NACK etc.) from viewer and forward to publisher
	go func() {
		buf := make([]byte, 1500)
		for {
			if _, _, rtcpErr := rtpSender.Read(buf); rtcpErr != nil {
				return
			}
			// Forward PLI/NACK to publisher for better encoding adaptation
			sendPLI(room)
		}
	}()

	pc.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		log.Printf("[sub:%s] ICE state: %s", subID, state.String())
		if state == webrtc.ICEConnectionStateConnected {
			sendPLI(room)
		}
		if state == webrtc.ICEConnectionStateFailed || state == webrtc.ICEConnectionStateDisconnected {
			pc.Close()
			// Cleanup from current room
			cleanupSub(subID)
		}
	})

	if err = pc.SetRemoteDescription(offer); err != nil {
		return nil, "", fmt.Errorf("set remote: %w", err)
	}

	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return nil, "", fmt.Errorf("create answer: %w", err)
	}

	gatherComplete := webrtc.GatheringCompletePromise(pc)
	if err = pc.SetLocalDescription(answer); err != nil {
		return nil, "", fmt.Errorf("set local: %w", err)
	}
	<-gatherComplete

	// Register subscriber
	room.mu.Lock()
	room.subscribers[subID] = sub
	room.mu.Unlock()

	allSubsMu.Lock()
	allSubs[subID] = sub
	allSubsMu.Unlock()

	localDesc := pc.LocalDescription()
	log.Printf("[sub:%s] answer ready, room=%s", subID, roomName)
	return localDesc, subID, nil
}

// sendPLI sends a Picture Loss Indication to the publisher of a room.
func sendPLI(room *Room) {
	room.mu.RLock()
	pubPC := room.publisher
	room.mu.RUnlock()
	if pubPC == nil {
		return
	}
	for _, receiver := range pubPC.GetReceivers() {
		if receiver.Track() != nil {
			pubPC.WriteRTCP([]rtcp.Packet{
				&rtcp.PictureLossIndication{
					MediaSSRC: uint32(receiver.Track().SSRC()),
				},
			})
		}
	}
}

// handlePLI handles POST /pli?room=xxx — forces a keyframe request to the publisher.
func handlePLI(w http.ResponseWriter, r *http.Request) {
	roomName := r.URL.Query().Get("room")
	if roomName == "" {
		http.Error(w, "missing room param", http.StatusBadRequest)
		return
	}
	room := getRoom(roomName)
	sendPLI(room)
	log.Printf("[pli] keyframe requested for room %s", roomName)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "room": roomName})
}

// cleanupSub removes a subscriber from its room and global registry.
func cleanupSub(subID string) {
	allSubsMu.Lock()
	sub, ok := allSubs[subID]
	if ok {
		delete(allSubs, subID)
	}
	allSubsMu.Unlock()

	if !ok {
		return
	}

	room := getRoom(sub.room)
	room.mu.Lock()
	delete(room.subscribers, subID)
	room.mu.Unlock()
}

// handleSwitch moves a subscriber from one room's track to another without re-negotiation.
func handleSwitch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}

	subID := r.URL.Query().Get("sub_id")
	newRoom := r.URL.Query().Get("room")
	if subID == "" || newRoom == "" {
		http.Error(w, "need ?sub_id=xxx&room=yyy", http.StatusBadRequest)
		return
	}

	allSubsMu.RLock()
	sub, ok := allSubs[subID]
	allSubsMu.RUnlock()
	if !ok {
		http.Error(w, "subscriber not found", http.StatusNotFound)
		return
	}

	targetRoom := getRoom(newRoom)
	targetRoom.mu.RLock()
	newTrack := targetRoom.videoTrack
	targetRoom.mu.RUnlock()

	if newTrack == nil {
		http.Error(w, "no publisher in target room", http.StatusNotFound)
		return
	}

	// Remove from old room
	oldRoom := getRoom(sub.room)
	oldRoom.mu.Lock()
	delete(oldRoom.subscribers, subID)
	oldRoom.mu.Unlock()

	// Replace track (no re-negotiation needed!)
	if err := sub.sender.ReplaceTrack(newTrack); err != nil {
		http.Error(w, fmt.Sprintf("replace track: %v", err), http.StatusInternalServerError)
		return
	}

	// Update subscriber's room
	sub.room = newRoom
	targetRoom.mu.Lock()
	targetRoom.subscribers[subID] = sub
	targetRoom.mu.Unlock()

	// Send PLI to new room's publisher for immediate keyframe
	sendPLI(targetRoom)

	log.Printf("[switch] %s → room %s", subID, newRoom)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "room": newRoom})
}

// handleRecording controls recording per room.
// POST /recording?room=xxx&action=start|stop
// GET  /recording?room=xxx  → status
func handleRecording(w http.ResponseWriter, r *http.Request) {
	roomName := r.URL.Query().Get("room")
	if roomName == "" {
		http.Error(w, "need ?room=xxx", http.StatusBadRequest)
		return
	}

	room := getRoom(roomName)
	w.Header().Set("Content-Type", "application/json")

	if r.Method == http.MethodGet {
		// Status
		room.mu.RLock()
		rec := room.recorder
		room.mu.RUnlock()

		status := map[string]interface{}{"room": roomName, "recording": false}
		if rec != nil {
			status["recording"] = true
			status["file"] = rec.FilePath()
		}
		json.NewEncoder(w).Encode(status)
		return
	}

	if r.Method != http.MethodPost {
		http.Error(w, "GET or POST only", http.StatusMethodNotAllowed)
		return
	}

	action := r.URL.Query().Get("action")
	switch action {
	case "start":
		room.mu.Lock()
		if room.recorder != nil {
			room.mu.Unlock()
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "already_recording",
				"file":   room.recorder.FilePath(),
			})
			return
		}
		rec := NewRecorder(roomName)
		room.recorder = rec
		room.mu.Unlock()

		log.Printf("[recording] started for room %s", roomName)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "started",
			"file":   rec.FilePath(),
		})

	case "stop":
		room.mu.Lock()
		rec := room.recorder
		room.recorder = nil
		room.mu.Unlock()

		if rec == nil {
			json.NewEncoder(w).Encode(map[string]string{"status": "not_recording"})
			return
		}
		rec.Close()
		log.Printf("[recording] stopped for room %s", roomName)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "stopped",
			"file":   rec.FilePath(),
		})

	default:
		http.Error(w, "need ?action=start|stop", http.StatusBadRequest)
	}
}
