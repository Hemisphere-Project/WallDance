/*
 * IDS Camera USB3 Stall Diagnostic — Pure C++ (No Python, No GIL)
 * =================================================================
 *
 * Purpose:  Determine whether the ~1650ms USB3 stalls observed in the Python
 *           application are caused by the Python runtime/SDK bindings, or
 *           whether they also occur with the native C++ IDS Peak API.
 *
 * IDS Cockpit (C++ native) never shows these stalls. If this test also
 * shows zero stalls, then the root cause is in the Python SDK bindings.
 * If stalls appear here too, it's a GenTL/USB3 driver issue.
 *
 * The test runs a tight acquisition loop for a configurable duration,
 * measuring inter-frame gaps and reporting stalls (> 400ms).
 *
 * Build:  see CMakeLists.txt and build.bat
 * Usage:  ids_stall_test.exe [duration_seconds] [buffer_count]
 *         defaults: 120s, 16 buffers
 */

#include <chrono>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include <peak/peak.hpp>

// ─────────────────────────────────────────────────────────────────
//  Configuration
// ─────────────────────────────────────────────────────────────────
static constexpr double STALL_THRESHOLD_S      = 0.400;   // 400 ms
static constexpr double SEVERE_STALL_THRESHOLD  = 1.000;   // 1 s
static constexpr double TARGET_FPS             = 20.0;
static constexpr double WARMUP_S               = 5.0;

// ─────────────────────────────────────────────────────────────────
//  Stall record
// ─────────────────────────────────────────────────────────────────
struct StallEvent {
    uint64_t frame_idx;
    double   gap_s;
    double   timestamp_s;   // seconds since acq start
};

// ─────────────────────────────────────────────────────────────────
//  High-resolution clock helper
// ─────────────────────────────────────────────────────────────────
using Clock = std::chrono::high_resolution_clock;

static double now_s()
{
    static auto epoch = Clock::now();
    return std::chrono::duration<double>(Clock::now() - epoch).count();
}

// ─────────────────────────────────────────────────────────────────
//  Helper: set node value (safe)
// ─────────────────────────────────────────────────────────────────
template <typename NodeT, typename ValT>
bool try_set_node(const std::shared_ptr<peak::core::NodeMap>& nm,
                  const std::string& name, ValT value)
{
    try {
        nm->FindNode<NodeT>(name)->SetValue(value);
        return true;
    } catch (...) {
        return false;
    }
}

template <typename NodeT>
bool try_set_entry(const std::shared_ptr<peak::core::NodeMap>& nm,
                   const std::string& nodeName, const std::string& entryName)
{
    try {
        auto node = nm->FindNode<NodeT>(nodeName);
        node->SetCurrentEntry(node->FindEntry(entryName));
        return true;
    } catch (...) {
        return false;
    }
}

// ─────────────────────────────────────────────────────────────────
//  Main
// ─────────────────────────────────────────────────────────────────
int main(int argc, char* argv[])
{
    // Parse arguments
    double duration_s   = 120.0;
    int    buffer_count = 16;

    if (argc > 1) duration_s   = std::stod(argv[1]);
    if (argc > 2) buffer_count = std::stoi(argv[2]);

    std::cout << "====================================================================\n"
              << "  IDS Camera USB3 Stall Diagnostic — Pure C++\n"
              << "====================================================================\n"
              << "  Duration       : " << duration_s << "s\n"
              << "  Buffer count   : " << buffer_count << "\n"
              << "  Stall threshold: " << (STALL_THRESHOLD_S * 1000) << "ms\n"
              << "  Warmup         : " << WARMUP_S << "s\n"
              << std::endl;

    // ── Initialize library ──────────────────────────────────────
    peak::Library::Initialize();
    auto& dm = peak::DeviceManager::Instance();

    try {
        dm.Update();
    } catch (const std::exception& e) {
        std::cerr << "DeviceManager::Update failed: " << e.what() << std::endl;
        peak::Library::Close();
        return 1;
    }

    if (dm.Devices().empty()) {
        std::cerr << "No IDS cameras found." << std::endl;
        peak::Library::Close();
        return 1;
    }

    // ── Open first available camera ─────────────────────────────
    std::shared_ptr<peak::core::Device> device;
    for (const auto& desc : dm.Devices()) {
        if (desc->IsOpenable()) {
            device = desc->OpenDevice(peak::core::DeviceAccessType::Control);
            std::cout << "  Camera         : " << desc->ModelName()
                      << " (SN: " << desc->SerialNumber() << ")\n";
            break;
        }
    }
    if (!device) {
        std::cerr << "No openable camera." << std::endl;
        peak::Library::Close();
        return 1;
    }

    auto nm = device->RemoteDevice()->NodeMaps().at(0);

    // ── Configure: continuous free-run ──────────────────────────
    try_set_entry<peak::core::nodes::EnumerationNode>(nm, "AcquisitionMode", "Continuous");
    try {
        try_set_entry<peak::core::nodes::EnumerationNode>(nm, "TriggerSelector", "FrameStart");
    } catch (...) {}
    try_set_entry<peak::core::nodes::EnumerationNode>(nm, "TriggerMode", "Off");

    // ── Pixel format ────────────────────────────────────────────
    try {
        auto pfNode = nm->FindNode<peak::core::nodes::EnumerationNode>("PixelFormat");
        std::cout << "  PixelFormat    : " << pfNode->CurrentEntry()->SymbolicValue() << "\n";
    } catch (...) {
        std::cout << "  PixelFormat    : (unknown)\n";
    }

    // ── Full resolution ─────────────────────────────────────────
    try {
        auto w = nm->FindNode<peak::core::nodes::IntegerNode>("Width");
        auto h = nm->FindNode<peak::core::nodes::IntegerNode>("Height");
        w->SetValue(w->Maximum());
        h->SetValue(h->Maximum());
    } catch (...) {}

    int64_t width = 0, height = 0;
    try { width  = nm->FindNode<peak::core::nodes::IntegerNode>("Width")->Value();  } catch (...) {}
    try { height = nm->FindNode<peak::core::nodes::IntegerNode>("Height")->Value(); } catch (...) {}
    std::cout << "  Resolution     : " << width << "x" << height << "\n";

    // ── FPS ─────────────────────────────────────────────────────
    double actual_fps = TARGET_FPS;
    try {
        auto fpsNode = nm->FindNode<peak::core::nodes::FloatNode>("AcquisitionFrameRate");
        double maxFps = fpsNode->Maximum();
        double target = std::min(TARGET_FPS, maxFps);
        fpsNode->SetValue(target);
        actual_fps = fpsNode->Value();
    } catch (...) {}
    std::cout << "  FPS            : " << actual_fps << "\n";

    // ── Exposure auto ───────────────────────────────────────────
    try_set_entry<peak::core::nodes::EnumerationNode>(nm, "ExposureAuto", "Continuous");

    // ── DeviceLinkThroughputLimit ───────────────────────────────
    try {
        auto tl = nm->FindNode<peak::core::nodes::IntegerNode>("DeviceLinkThroughputLimit");
        std::cout << "  ThroughputLimit: " << (tl->Value() / 1000000) << " MB/s\n";
    } catch (...) {}

    // ── Open data stream ────────────────────────────────────────
    if (device->DataStreams().empty()) {
        std::cerr << "No data streams." << std::endl;
        peak::Library::Close();
        return 1;
    }
    auto datastream = device->DataStreams().at(0)->OpenDataStream();
    auto dsNm = datastream->NodeMaps().at(0);

    // ── Buffer handling: NewestOnly ─────────────────────────────
    try_set_entry<peak::core::nodes::EnumerationNode>(dsNm, "StreamBufferHandlingMode", "NewestOnly");
    std::cout << "  BufferHandling : NewestOnly\n";

    // ── Allocate buffers ────────────────────────────────────────
    int64_t payload_size = nm->FindNode<peak::core::nodes::IntegerNode>("PayloadSize")->Value();

    datastream->Flush(peak::core::DataStreamFlushMode::DiscardAll);
    for (const auto& buf : datastream->AnnouncedBuffers()) {
        datastream->RevokeBuffer(buf);
    }
    for (int i = 0; i < buffer_count; ++i) {
        datastream->AllocAndAnnounceBuffer(static_cast<size_t>(payload_size), nullptr);
    }
    std::cout << "  Buffers        : " << buffer_count << " x " << payload_size << " bytes\n";

    // ── Queue all buffers ───────────────────────────────────────
    for (const auto& buf : datastream->AnnouncedBuffers()) {
        datastream->QueueBuffer(buf);
    }

    // ── Lock transport layer params ─────────────────────────────
    try_set_node<peak::core::nodes::IntegerNode>(nm, "TLParamsLocked", int64_t(1));

    // ── Start acquisition ───────────────────────────────────────
    datastream->StartAcquisition();
    nm->FindNode<peak::core::nodes::CommandNode>("AcquisitionStart")->Execute();

    std::cout << "\n  Acquisition started. Running for "
              << (WARMUP_S + duration_s) << "s (" << WARMUP_S << "s warmup + "
              << duration_s << "s measurement)...\n" << std::endl;

    // ── Acquisition loop ────────────────────────────────────────
    uint64_t timeout_ms = static_cast<uint64_t>(
        std::max(150.0, std::min(500.0, 5000.0 / std::max(1.0, actual_fps))));

    uint64_t frame_count        = 0;
    uint64_t warmup_frames      = 0;
    uint64_t timeout_count      = 0;
    uint64_t incomplete_count   = 0;

    std::vector<StallEvent> stalls;
    std::vector<StallEvent> warmup_stalls;
    std::vector<double>     gaps;  // measurement-period only

    auto acq_start     = Clock::now();
    auto last_frame_tp = acq_start;

    double total_run = WARMUP_S + duration_s;

    while (true) {
        auto elapsed = std::chrono::duration<double>(Clock::now() - acq_start).count();
        if (elapsed >= total_run) break;

        std::shared_ptr<peak::core::Buffer> buffer;
        try {
            buffer = datastream->WaitForFinishedBuffer(timeout_ms);
        }
        catch (const peak::core::TimeoutException&) {
            timeout_count++;
            continue;
        }
        catch (const peak::core::AbortedException&) {
            break;
        }
        catch (const std::exception& e) {
            std::cerr << "WaitForFinishedBuffer error: " << e.what() << std::endl;
            continue;
        }

        auto now_tp = Clock::now();
        double gap = std::chrono::duration<double>(now_tp - last_frame_tp).count();
        last_frame_tp = now_tp;
        elapsed = std::chrono::duration<double>(now_tp - acq_start).count();

        bool in_warmup = elapsed < WARMUP_S;

        // Check buffer state
        bool incomplete = false;
        try { incomplete = buffer->IsIncomplete(); } catch (...) {}
        if (incomplete) incomplete_count++;

        // Access the raw data pointer (just like our Python test does memcpy)
        // This is to ensure the DMA transfer is complete and we actually "touch" the data.
        volatile uint8_t firstByte = 0;
        try {
            void* ptr = buffer->BasePtr();
            if (ptr) {
                firstByte = *reinterpret_cast<uint8_t*>(ptr);
            }
        } catch (...) {}
        (void)firstByte;

        // Re-queue buffer immediately (minimal hold time)
        try {
            datastream->QueueBuffer(buffer);
        } catch (const std::exception& e) {
            std::cerr << "QueueBuffer error: " << e.what() << std::endl;
        }

        // Record statistics
        if (in_warmup) {
            warmup_frames++;
            if (gap > STALL_THRESHOLD_S) {
                warmup_stalls.push_back({warmup_frames, gap, elapsed});
            }
        } else {
            frame_count++;
            gaps.push_back(gap);
            if (gap > STALL_THRESHOLD_S) {
                stalls.push_back({frame_count, gap, elapsed});
                const char* severity = (gap >= SEVERE_STALL_THRESHOLD) ? "SEVERE" : "stall";
                std::cout << "  [" << severity << "] frame " << frame_count
                          << ": " << std::fixed << std::setprecision(0)
                          << (gap * 1000) << "ms gap at t="
                          << std::setprecision(1) << elapsed << "s" << std::endl;
            }
        }
    }

    // ── Stop acquisition ────────────────────────────────────────
    try { datastream->KillWait(); } catch (...) {}
    try { nm->FindNode<peak::core::nodes::CommandNode>("AcquisitionStop")->Execute(); } catch (...) {}
    try { datastream->StopAcquisition(); } catch (...) {}
    try_set_node<peak::core::nodes::IntegerNode>(nm, "TLParamsLocked", int64_t(0));

    // ── Compute statistics ──────────────────────────────────────
    double avg_gap_ms = 0, max_gap_ms = 0, avg_fps = 0;
    if (!gaps.empty()) {
        double sum = 0;
        for (double g : gaps) { sum += g; if (g > max_gap_ms) max_gap_ms = g; }
        avg_gap_ms = (sum / gaps.size()) * 1000.0;
        max_gap_ms *= 1000.0;
        avg_fps = static_cast<double>(frame_count) / duration_s;
    }

    int severe_count = 0;
    for (const auto& s : stalls) {
        if (s.gap_s >= SEVERE_STALL_THRESHOLD) severe_count++;
    }

    // ── Print results ───────────────────────────────────────────
    std::cout << "\n====================================================================\n"
              << "  RESULTS — Pure C++ IDS Acquisition\n"
              << "====================================================================\n"
              << "  Duration    : " << duration_s << "s\n"
              << "  Frames      : " << frame_count << "\n"
              << "  FPS         : " << std::fixed << std::setprecision(1) << avg_fps << "\n"
              << "  Stalls      : " << stalls.size() << " total, "
              << severe_count << " severe (>=" << (SEVERE_STALL_THRESHOLD * 1000) << "ms)\n"
              << "  Gaps        : avg=" << std::setprecision(1) << avg_gap_ms
              << "ms, max=" << std::setprecision(0) << max_gap_ms << "ms\n"
              << "  Timeouts    : " << timeout_count << "\n"
              << "  Incomplete  : " << incomplete_count << "\n"
              << "  Warmup      : " << warmup_frames << " frames, "
              << warmup_stalls.size() << " stalls (ignored)\n"
              << std::endl;

    // Stall details
    if (!stalls.empty()) {
        std::cout << "  STALL DETAILS:\n";
        for (const auto& s : stalls) {
            const char* sev = (s.gap_s >= SEVERE_STALL_THRESHOLD) ? "SEVERE" : "stall ";
            std::cout << "    frame " << std::setw(5) << s.frame_idx
                      << ": " << std::setprecision(0) << (s.gap_s * 1000)
                      << "ms [" << sev << "] at t=" << std::setprecision(1)
                      << s.timestamp_s << "s\n";
        }
        std::cout << std::endl;
    }

    // ── Verdict ─────────────────────────────────────────────────
    std::cout << "  ══════════════════════════════════════════════════════════\n";
    if (stalls.empty()) {
        std::cout << "  VERDICT: ZERO stalls in pure C++!\n"
                  << "  The Python SDK bindings are the root cause.\n"
                  << "  Solution: C++ acquisition wrapper for Python.\n";
    } else if (static_cast<int>(stalls.size()) <= 1 && duration_s >= 60) {
        std::cout << "  VERDICT: Minimal stalls (" << stalls.size() << ") in C++.\n"
                  << "  Significantly fewer than Python (~2-6/min).\n"
                  << "  The Python SDK adds latency. C++ wrapper recommended.\n";
    } else {
        double stalls_per_min = stalls.size() / (duration_s / 60.0);
        std::cout << "  VERDICT: " << stalls.size() << " stalls ("
                  << std::setprecision(1) << stalls_per_min << "/min) in C++.\n"
                  << "  Compare with Python: ~2-6/min.\n";
        if (stalls_per_min < 1.5) {
            std::cout << "  C++ has FEWER stalls than Python. Partial improvement.\n";
        } else {
            std::cout << "  Similar rate to Python. Root cause is in GenTL/USB3 driver,\n"
                      << "  not the Python bindings.\n";
        }
    }
    std::cout << "  ══════════════════════════════════════════════════════════\n"
              << std::endl;

    // ── Cleanup ─────────────────────────────────────────────────
    try {
        datastream->Flush(peak::core::DataStreamFlushMode::DiscardAll);
        for (const auto& buf : datastream->AnnouncedBuffers()) {
            datastream->RevokeBuffer(buf);
        }
    } catch (...) {}

    datastream.reset();
    device.reset();
    peak::Library::Close();

    std::cout << "  Done. Camera closed." << std::endl;

#ifdef _WIN32
    std::cout << "\nPress Enter to exit..." << std::endl;
    std::cin.get();
#endif

    return 0;
}
