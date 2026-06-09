package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/at-wat/ebml-go/webm"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
	"github.com/pion/webrtc/v4/pkg/media/samplebuilder"
)

// RecordingDir is the base directory for recordings.
var RecordingDir = "/data/recordings"

func init() {
	if d := os.Getenv("RECORDING_DIR"); d != "" {
		RecordingDir = d
	}
}

// Recorder writes incoming VP8 RTP packets to a WebM file.
type Recorder struct {
	mu sync.Mutex

	room     string
	filePath string
	file     *os.File

	videoWriter    webm.BlockWriteCloser
	vp8Builder     *samplebuilder.SampleBuilder
	videoTimestamp time.Duration

	started   bool
	closed    bool
	closing   bool
	pktCount  uint64
	dropCount uint64

	pktCh      chan *rtp.Packet
	done       chan struct{}
	workerDone chan struct{}
	closeOnce  sync.Once
}

// NewRecorder creates a recorder for a room.
// Does NOT start writing until the first keyframe arrives.
func NewRecorder(room string) *Recorder {
	// Create directory
	dir := filepath.Join(RecordingDir, room)
	os.MkdirAll(dir, 0o755)

	ts := time.Now().Format("2006-01-02T15-04-05")
	filename := fmt.Sprintf("%s_%s.webm", room, ts)
	fp := filepath.Join(dir, filename)

	r := &Recorder{
		room:       room,
		filePath:   fp,
		vp8Builder: samplebuilder.New(64, &codecs.VP8Packet{}, 90000),
		pktCh:      make(chan *rtp.Packet, 2048),
		done:       make(chan struct{}),
		workerDone: make(chan struct{}),
	}
	go r.run()
	return r
}

// PushRTP feeds an RTP packet into the recorder.
// Non-blocking; called from the publisher's read loop.
func (r *Recorder) PushRTP(pkt *rtp.Packet) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.closing || r.closed {
		return
	}
	select {
	case r.pktCh <- pkt:
		return
	default:
		r.dropCount++
		if r.dropCount == 1 || r.dropCount%300 == 0 {
			log.Printf("[recorder:%s] queue full, dropped %d RTP packets", r.room, r.dropCount)
		}
	}
}

func (r *Recorder) run() {
	defer close(r.workerDone)
	for {
		select {
		case pkt := <-r.pktCh:
			r.writeRTP(pkt)
		case <-r.done:
			for {
				select {
				case pkt := <-r.pktCh:
					r.writeRTP(pkt)
				default:
					r.finalize()
					return
				}
			}
		}
	}
}

func (r *Recorder) writeRTP(pkt *rtp.Packet) {
	r.vp8Builder.Push(pkt)

	for {
		sample := r.vp8Builder.Pop()
		if sample == nil {
			return
		}

		if len(sample.Data) == 0 {
			continue
		}

		// VP8 keyframe detection: bit 0 of first byte == 0
		videoKeyframe := (sample.Data[0] & 0x1) == 0

		// Initialize WebM writer on first keyframe
		if !r.started && videoKeyframe {
			if err := r.initWriter(sample.Data); err != nil {
				log.Printf("[recorder:%s] init error: %v", r.room, err)
				return
			}
		}

		if r.videoWriter != nil {
			r.videoTimestamp += sample.Duration
			if _, err := r.videoWriter.Write(videoKeyframe, int64(r.videoTimestamp/time.Millisecond), sample.Data); err != nil {
				log.Printf("[recorder:%s] write error: %v", r.room, err)
				return
			}
			r.pktCount++
		}
	}
}

// initWriter opens the WebM file and creates the writer.
func (r *Recorder) initWriter(firstFrame []byte) error {
	// Parse VP8 keyframe header for width/height
	if len(firstFrame) < 10 {
		return fmt.Errorf("keyframe too short")
	}
	raw := uint(firstFrame[6]) | uint(firstFrame[7])<<8 | uint(firstFrame[8])<<16 | uint(firstFrame[9])<<24
	width := int(raw & 0x3FFF)
	height := int((raw >> 16) & 0x3FFF)

	f, err := os.OpenFile(r.filePath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return fmt.Errorf("open file: %w", err)
	}
	r.file = f

	ws, err := webm.NewSimpleBlockWriter(f,
		[]webm.TrackEntry{
			{
				Name:            "Video",
				TrackNumber:     1,
				TrackUID:        67890,
				CodecID:         "V_VP8",
				TrackType:       1,
				DefaultDuration: 33333333, // ~30fps
				Video: &webm.Video{
					PixelWidth:  uint64(width),
					PixelHeight: uint64(height),
				},
			},
		})
	if err != nil {
		f.Close()
		return fmt.Errorf("webm writer: %w", err)
	}

	r.videoWriter = ws[0]
	r.started = true
	log.Printf("[recorder:%s] started: %s (%dx%d)", r.room, r.filePath, width, height)
	return nil
}

// Close finalizes the WebM file.
func (r *Recorder) Close() {
	r.closeOnce.Do(func() {
		r.mu.Lock()
		r.closing = true
		r.mu.Unlock()
		close(r.done)
		<-r.workerDone
		log.Printf("[recorder:%s] closed: %s (%d frames, %d dropped RTP)", r.room, r.filePath, r.pktCount, r.dropCount)
	})
}

func (r *Recorder) finalize() {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.closed {
		return
	}
	r.closed = true

	if r.videoWriter != nil {
		r.videoWriter.Close()
	}
	if r.file != nil {
		r.file.Close()
	}
}

// FilePath returns the recording file path.
func (r *Recorder) FilePath() string {
	return r.filePath
}
