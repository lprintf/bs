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
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// Room holds a publisher stream and subscribers currently viewing it.
type Room struct {
	mu          sync.RWMutex
	videoTrack  *webrtc.TrackLocalStaticRTP
	publisher   *webrtc.PeerConnection
	subscribers map[string]*Subscriber // id -> subscriber
	recorder    *Recorder              // WebM recorder (nil = not recording)
	// RTP sequence/timestamp continuity across publisher reconnects
	seqOffset    uint16
	tsOffset     uint32
	lastSeq      uint16
	lastTS       uint32
	seqReset     bool
	lastPacketAt time.Time
}

// Subscriber holds a subscriber connection and its stable outbound RTP stream.
type Subscriber struct {
	mu          sync.Mutex
	id          string
	pc          *webrtc.PeerConnection
	sender      *webrtc.RTPSender
	outputTrack *webrtc.TrackLocalStaticRTP
	room        string // current source room name
	codecMime   string

	// Per-subscriber RTP continuity. These offsets are reset on every source
	// switch so the browser sees one continuous RTP stream.
	seqOffset    uint16
	tsOffset     uint32
	lastSeq      uint16
	lastTS       uint32
	hasWritten   bool
	needKeyframe bool
	lastPLIAt    time.Time
	closed       bool
}

var (
	rooms   = map[string]*Room{}
	roomsMu sync.RWMutex
	api     *webrtc.API
	// Global subscriber registry for cross-room switching
	allSubs      = map[string]*Subscriber{}
	allSubsMu    sync.RWMutex
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

	// --- MediaEngine: register H.264 + VP8 + VP9 + AV1 ---
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
		// VP8 (used by the WebM recorder)
		{
			RTPCodecCapability: webrtc.RTPCodecCapability{
				MimeType:  webrtc.MimeTypeVP8,
				ClockRate: 90000,
			},
			PayloadType: 96,
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
	log.Printf("MediaEngine: H.264 + VP8 + VP9 + AV1 registered (publisher SDP controls selection)")

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
	http.HandleFunc("/ready", handleReady)
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
		answer, err = handlePublisher(roomName, room, offer)
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

func handlePublisher(roomName string, room *Room, offer webrtc.SessionDescription) (*webrtc.SessionDescription, error) {
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, fmt.Errorf("new pc: %w", err)
	}

	// Detect preferred codec from offer SDP
	preferredMime := webrtc.MimeTypeH264 // default
	if strings.Contains(offer.SDP, "VP8/90000") && !strings.Contains(offer.SDP, "H264/90000") {
		preferredMime = webrtc.MimeTypeVP8
	} else if strings.Contains(offer.SDP, "VP9/90000") && !strings.Contains(offer.SDP, "H264/90000") {
		preferredMime = webrtc.MimeTypeVP9
	} else if strings.Contains(offer.SDP, "AV1/90000") && !strings.Contains(offer.SDP, "H264/90000") && !strings.Contains(offer.SDP, "VP8/90000") && !strings.Contains(offer.SDP, "VP9/90000") {
		preferredMime = webrtc.MimeTypeAV1
	}

	// Create or update codec metadata for the room. Subscribers do not bind to
	// this track directly; each subscriber has its own stable outbound track.
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
			room.lastPacketAt = time.Now()
			room.mu.Unlock()

			// Tee to recorder (clone to avoid buf reuse corruption)
			room.mu.RLock()
			rec := room.recorder
			room.mu.RUnlock()
			if rec != nil {
				rec.PushRTP(cloneRTPPacket(pkt))
			}

			pktCount++
			if isKeyframeStart(codecMime, pkt) {
				kfCount++
			}
			if pktCount%300 == 0 {
				log.Printf("[pub] RTP stats: %d pkts, %d keyframes (%.1f%%)", pktCount, kfCount, float64(kfCount)/float64(pktCount)*100)
			}

			forwardSourcePacket(roomName, room, codecMime, pkt)
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
	sourceTrack := room.videoTrack
	room.mu.RUnlock()

	if sourceTrack == nil {
		return nil, "", fmt.Errorf("no publisher in room yet")
	}
	codecMime := sourceTrack.Codec().MimeType

	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return nil, "", fmt.Errorf("new pc: %w", err)
	}

	outputTrack, err := webrtc.NewTrackLocalStaticRTP(
		webrtc.RTPCodecCapability{MimeType: codecMime},
		"video", "tabshare",
	)
	if err != nil {
		return nil, "", fmt.Errorf("new subscriber track: %w", err)
	}

	rtpSender, err := pc.AddTrack(outputTrack)
	if err != nil {
		return nil, "", fmt.Errorf("add track: %w", err)
	}

	subID := nextSubID()
	sub := &Subscriber{
		id:           subID,
		pc:           pc,
		sender:       rtpSender,
		outputTrack:  outputTrack,
		room:         roomName,
		codecMime:    codecMime,
		needKeyframe: true,
	}

	// Read RTCP (PLI, NACK etc.) from viewer and forward to publisher
	go func() {
		buf := make([]byte, 1500)
		for {
			if _, _, rtcpErr := rtpSender.Read(buf); rtcpErr != nil {
				return
			}
			// Forward viewer feedback to the current publisher.
			sendPLI(sub.currentRoom())
		}
	}()

	pc.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		log.Printf("[sub:%s] ICE state: %s", subID, state.String())
		if state == webrtc.ICEConnectionStateConnected {
			sendPLI(sub.currentRoom())
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

	sub.mu.Lock()
	sub.closed = true
	roomName := sub.room
	sub.mu.Unlock()

	room := getRoom(roomName)
	room.mu.Lock()
	delete(room.subscribers, subID)
	room.mu.Unlock()
}

func (s *Subscriber) currentRoom() *Room {
	s.mu.Lock()
	roomName := s.room
	s.mu.Unlock()
	return getRoom(roomName)
}

func forwardSourcePacket(roomName string, room *Room, codecMime string, pkt *rtp.Packet) {
	room.mu.RLock()
	subs := make([]*Subscriber, 0, len(room.subscribers))
	for _, sub := range room.subscribers {
		subs = append(subs, sub)
	}
	room.mu.RUnlock()

	for _, sub := range subs {
		sub.writeSourceRTP(roomName, codecMime, pkt)
	}
}

func (s *Subscriber) writeSourceRTP(sourceRoom string, codecMime string, pkt *rtp.Packet) {
	var requestPLI bool
	var writeErr error

	s.mu.Lock()
	if s.closed || s.room != sourceRoom {
		s.mu.Unlock()
		return
	}
	if s.codecMime != codecMime {
		s.mu.Unlock()
		return
	}

	if s.needKeyframe && !isKeyframeStart(codecMime, pkt) {
		if time.Since(s.lastPLIAt) > 700*time.Millisecond {
			s.lastPLIAt = time.Now()
			requestPLI = true
		}
		s.mu.Unlock()
		if requestPLI {
			sendPLI(getRoom(sourceRoom))
		}
		return
	}

	out := cloneRTPPacket(pkt)
	if s.needKeyframe {
		if s.hasWritten {
			s.seqOffset = s.lastSeq - out.Header.SequenceNumber + 1
			s.tsOffset = s.lastTS - out.Header.Timestamp + 1
		} else {
			s.seqOffset = 0
			s.tsOffset = 0
		}
		s.needKeyframe = false
		log.Printf("[sub:%s] source ready: room=%s codec=%s seqOffset=%d tsOffset=%d", s.id, sourceRoom, codecMime, s.seqOffset, s.tsOffset)
	}

	out.Header.SequenceNumber += s.seqOffset
	out.Header.Timestamp += s.tsOffset
	s.lastSeq = out.Header.SequenceNumber
	s.lastTS = out.Header.Timestamp
	s.hasWritten = true
	writeErr = s.outputTrack.WriteRTP(out)
	s.mu.Unlock()

	if writeErr != nil {
		log.Printf("[sub:%s] track write error: %v", s.id, writeErr)
	}
}

func cloneRTPPacket(pkt *rtp.Packet) *rtp.Packet {
	cloned := &rtp.Packet{
		Header:  pkt.Header,
		Payload: make([]byte, len(pkt.Payload)),
	}
	cloned.Header.Extensions = append([]rtp.Extension(nil), pkt.Header.Extensions...)
	copy(cloned.Payload, pkt.Payload)
	return cloned
}

func isKeyframeStart(codecMime string, pkt *rtp.Packet) bool {
	switch strings.ToLower(codecMime) {
	case strings.ToLower(webrtc.MimeTypeH264):
		return isH264KeyframeStart(pkt.Payload)
	case strings.ToLower(webrtc.MimeTypeVP8):
		return len(pkt.Payload) > 0 && (pkt.Payload[0]&0x1) == 0
	case strings.ToLower(webrtc.MimeTypeVP9):
		return isVP9KeyframeStart(pkt.Payload)
	case strings.ToLower(webrtc.MimeTypeAV1):
		return true
	default:
		return true
	}
}

func isH264KeyframeStart(payload []byte) bool {
	if len(payload) == 0 {
		return false
	}
	nalType := payload[0] & 0x1F
	switch nalType {
	case 5, 7, 8:
		return true
	case 24: // STAP-A
		offset := 1
		for offset+2 <= len(payload) {
			size := int(payload[offset])<<8 | int(payload[offset+1])
			offset += 2
			if size <= 0 || offset+size > len(payload) {
				return false
			}
			innerType := payload[offset] & 0x1F
			if innerType == 5 || innerType == 7 || innerType == 8 {
				return true
			}
			offset += size
		}
	case 28: // FU-A
		if len(payload) < 2 {
			return false
		}
		isStart := (payload[1] & 0x80) != 0
		fuNalType := payload[1] & 0x1F
		return isStart && fuNalType == 5
	}
	return false
}

func isVP9KeyframeStart(payload []byte) bool {
	if len(payload) == 0 {
		return false
	}
	descriptor := payload[0]
	interPicturePredicted := (descriptor & 0x40) != 0
	beginningOfFrame := (descriptor & 0x08) != 0
	return beginningOfFrame && !interPicturePredicted
}

func waitForFreshTrack(room *Room, timeout time.Duration) (*webrtc.TrackLocalStaticRTP, bool) {
	deadline := time.Now().Add(timeout)
	for {
		room.mu.RLock()
		track := room.videoTrack
		lastPacketAt := room.lastPacketAt
		fresh := track != nil && !lastPacketAt.IsZero() && time.Since(lastPacketAt) < 3*time.Second
		room.mu.RUnlock()

		if fresh {
			return track, true
		}
		if timeout <= 0 || time.Now().After(deadline) {
			return nil, false
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// handleReady waits until a room has received recent publisher RTP.
func handleReady(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "GET or POST only", http.StatusMethodNotAllowed)
		return
	}

	roomName := r.URL.Query().Get("room")
	timeoutMs := 0
	if raw := r.URL.Query().Get("timeout_ms"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			timeoutMs = parsed
		}
	}
	if roomName == "" {
		http.Error(w, "need ?room=xxx", http.StatusBadRequest)
		return
	}

	room := getRoom(roomName)
	if _, ok := waitForFreshTrack(room, time.Duration(timeoutMs)*time.Millisecond); !ok {
		http.Error(w, "no publisher in room", http.StatusNotFound)
		return
	}

	room.mu.RLock()
	lastPacketAt := room.lastPacketAt
	room.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":         "ready",
		"room":           roomName,
		"last_packet_at": lastPacketAt.Format(time.RFC3339Nano),
	})
}

// handleSwitch moves a subscriber from one room's track to another without re-negotiation.
func handleSwitch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}

	subID := r.URL.Query().Get("sub_id")
	newRoom := r.URL.Query().Get("room")
	timeoutMs := 0
	if raw := r.URL.Query().Get("timeout_ms"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			timeoutMs = parsed
		}
	}
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
	newTrack, ok := waitForFreshTrack(targetRoom, time.Duration(timeoutMs)*time.Millisecond)
	if !ok {
		http.Error(w, "no publisher in target room", http.StatusNotFound)
		return
	}
	targetCodec := newTrack.Codec().MimeType

	sub.mu.Lock()
	if sub.closed {
		sub.mu.Unlock()
		http.Error(w, "subscriber closed", http.StatusNotFound)
		return
	}
	if sub.codecMime != targetCodec {
		sub.mu.Unlock()
		http.Error(w, fmt.Sprintf("codec mismatch: subscriber=%s target=%s", sub.codecMime, targetCodec), http.StatusConflict)
		return
	}
	oldRoomName := sub.room
	sub.room = newRoom
	sub.needKeyframe = true
	sub.lastPLIAt = time.Now()
	sub.mu.Unlock()

	// Move the subscriber between room registries. Packet forwarding checks the
	// subscriber's current room as well, so stale snapshots from the old room are
	// dropped after this point.
	oldRoom := getRoom(oldRoomName)
	oldRoom.mu.Lock()
	delete(oldRoom.subscribers, subID)
	oldRoom.mu.Unlock()

	targetRoom.mu.Lock()
	targetRoom.subscribers[subID] = sub
	targetRoom.mu.Unlock()

	// Send PLI to new room's publisher for immediate keyframe
	sendPLI(targetRoom)

	log.Printf("[switch] %s %s → room %s", subID, oldRoomName, newRoom)

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
