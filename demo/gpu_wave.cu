// Modulated GPU load for Ground Control demo recordings.
//
// An idle GPU panel is three flat lines and a 0% bar. This drives utilization,
// VRAM, power draw, clocks and temperature with the same quasi-periodic
// waveform loadgen.py uses for the CPU, so the GPU plot has a shape.
//
// It also shows up as a real process in nvidia-smi, which is what makes the
// GPU widget's process rows worth recording.
//
//   nvcc -O2 -o gpu_wave gpu_wave.cu
//   ./gpu_wave --base=0.55 --amp=0.35 --seconds=60 --vram-gb=8
//
// Every argument is optional. It exits cleanly on SIGINT/SIGTERM and frees
// everything it allocated.

#include <cuda_runtime.h>

#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

namespace {

constexpr double kSlowPeriod = 23.0;  // matches loadgen.py
constexpr double kFastPeriod = 7.3;
constexpr double kSlice = 0.12;       // duty-cycle window, seconds
constexpr int kBurnIters = 96;        // arithmetic per element per launch

volatile std::sig_atomic_t g_stop = 0;

void OnSignal(int) { g_stop = 1; }

// Enough arithmetic per element to be compute-bound, with a dependency chain
// so the compiler cannot hoist it out of the loop.
__global__ void Burn(float* buf, size_t n, int iters) {
  size_t i = blockIdx.x * static_cast<size_t>(blockDim.x) + threadIdx.x;
  if (i >= n) return;
  float x = buf[i];
  for (int k = 0; k < iters; ++k) {
    x = fmaf(x, 1.0000001f, 1e-7f);
    x = fmaf(x, 0.9999999f, 1e-7f);
  }
  buf[i] = x;
}

// Streams over the whole buffer so the memory interface is busy too -- without
// this the GPU widget's memory-bandwidth reading stays near zero and the panel
// tells only half the story.
__global__ void Stream(const float* src, float* dst, size_t n) {
  size_t i = blockIdx.x * static_cast<size_t>(blockDim.x) + threadIdx.x;
  if (i < n) dst[i] = src[i] * 1.0001f + 0.5f;
}

double Waveform(double t, double base, double amp, double phase = 0.0) {
  const double slow = std::sin(2.0 * M_PI * (t / kSlowPeriod - phase));
  const double fast = std::sin(2.0 * M_PI * (t / kFastPeriod - phase * 1.7));
  double v = base + amp * (0.72 * slow + 0.28 * fast);
  if (v < 0.02) v = 0.02;
  if (v > 1.0) v = 1.0;
  return v;
}

double ArgValue(int argc, char** argv, const char* name, double fallback) {
  const size_t len = std::strlen(name);
  for (int i = 1; i < argc; ++i) {
    if (std::strncmp(argv[i], name, len) == 0 && argv[i][len] == '=') {
      return std::atof(argv[i] + len + 1);
    }
  }
  return fallback;
}

bool Check(cudaError_t err, const char* what) {
  if (err == cudaSuccess) return true;
  std::fprintf(stderr, "gpu_wave: %s: %s\n", what, cudaGetErrorString(err));
  return false;
}

}  // namespace

int main(int argc, char** argv) {
  const double base = ArgValue(argc, argv, "--base", 0.55);
  const double amp = ArgValue(argc, argv, "--amp", 0.35);
  const double seconds = ArgValue(argc, argv, "--seconds", 60.0);
  const double vram_gb = ArgValue(argc, argv, "--vram-gb", 8.0);
  // Several instances with different phases make the GPU panel's process rows
  // look like what they would on a shared machine, instead of one flat job.
  const double phase = ArgValue(argc, argv, "--phase", 0.0);

  std::signal(SIGINT, OnSignal);
  std::signal(SIGTERM, OnSignal);

  // Working set for the kernels: small enough to stay resident, large enough
  // to fill the SMs.
  const size_t work_n = 32u << 20;  // 32M floats = 128 MB
  float* work = nullptr;
  float* work_dst = nullptr;
  if (!Check(cudaMalloc(&work, work_n * sizeof(float)), "cudaMalloc work") ||
      !Check(cudaMalloc(&work_dst, work_n * sizeof(float)), "cudaMalloc dst")) {
    return 1;
  }
  Check(cudaMemset(work, 1, work_n * sizeof(float)), "cudaMemset");

  // Ballast blocks, allocated and freed over time so the VRAM trace moves
  // instead of sitting at one level for the whole recording.
  const size_t ballast_block = 512u << 20;  // 512 MB
  const int max_ballast = static_cast<int>((vram_gb * 1024.0) / 512.0);
  std::vector<void*> ballast;

  const int threads = 256;
  const int blocks = static_cast<int>((work_n + threads - 1) / threads);

  const auto t0 = std::chrono::steady_clock::now();
  auto elapsed = [&t0]() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0)
        .count();
  };

  while (!g_stop && elapsed() < seconds) {
    const double t = elapsed();
    const double duty = Waveform(t, base, amp, phase);

    // VRAM follows a slower envelope than utilization, the way a training job
    // grows its cache in steps while its SM load oscillates every batch.
    const int want = static_cast<int>(std::lround(
        max_ballast * (0.35 + 0.65 * Waveform(t * 0.35, 0.5, 0.45, phase))));
    while (static_cast<int>(ballast.size()) < want) {
      void* p = nullptr;
      if (cudaMalloc(&p, ballast_block) != cudaSuccess) break;
      cudaMemset(p, 0, ballast_block);
      ballast.push_back(p);
    }
    while (static_cast<int>(ballast.size()) > want) {
      cudaFree(ballast.back());
      ballast.pop_back();
    }

    // Keep the GPU busy for duty*kSlice, then leave it alone for the rest of
    // the slice. NVML samples "utilization" as the fraction of time a kernel
    // was resident, so this is what lands the reading on `duty` -- launching
    // one kernel per slice and sleeping the remainder does not, because a
    // single launch finishes in a millisecond and reads back as near-idle.
    const double busy_for = kSlice * duty;
    const auto busy_start = std::chrono::steady_clock::now();
    bool failed = false;
    do {
      Burn<<<blocks, threads>>>(work, work_n, kBurnIters);
      Stream<<<blocks, threads>>>(work, work_dst, work_n);
      if (cudaDeviceSynchronize() != cudaSuccess) {
        failed = true;
        break;
      }
    } while (std::chrono::duration<double>(std::chrono::steady_clock::now() -
                                           busy_start).count() < busy_for);
    if (failed) break;

    const double idle = kSlice - std::chrono::duration<double>(
        std::chrono::steady_clock::now() - busy_start).count();
    if (idle > 0.002) {
      std::this_thread::sleep_for(std::chrono::duration<double>(idle));
    }
  }

  for (void* p : ballast) cudaFree(p);
  cudaFree(work_dst);
  cudaFree(work);
  return 0;
}
