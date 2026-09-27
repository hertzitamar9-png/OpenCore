#pragma once
// Shared by the real frozen LM heads and the small CPU integration probe.
#include "ggml.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <memory>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace twincore {

inline bool capture_last_hidden(const ggml_tensor * tensor, int width, std::vector<float> & hidden) {
    if (!tensor || width < 1 || tensor->type != GGML_TYPE_F32 || tensor->ne[0] != width
            || tensor->ne[1] < 0 || tensor->ne[2] != 1 || tensor->ne[3] != 1
            || tensor->nb[0] != sizeof(float))
        throw std::runtime_error("Unexpected native final hidden-state geometry");
    // Long prefills span microbatches. Earlier batches have no requested
    // outputs; the final batch supplies the last hidden row for prediction.
    if (tensor->ne[1] == 0) return false;
    hidden.resize(width);
    ggml_backend_tensor_get(tensor, hidden.data(),
        (size_t)(tensor->ne[1] - 1) * tensor->nb[1], width * sizeof(float));
    return true;
}

inline bool tensor_on_gpu(const ggml_tensor * tensor) {
    if (!tensor || !tensor->buffer) return false;
    auto * device = ggml_backend_buft_get_device(ggml_backend_buffer_get_type(tensor->buffer));
    // CPU memory-mapped buffers have no associated device in the pinned ABI.
    return device && ggml_backend_dev_type(device) == GGML_BACKEND_DEVICE_TYPE_GPU;
}

inline ggml_backend_t open_head_backend(const ggml_tensor * tensor) {
    if (!tensor || !tensor->buffer) throw std::runtime_error("Native head buffer is unavailable");
    auto * type = ggml_backend_buffer_get_type(tensor->buffer);
    auto * device = ggml_backend_buft_get_device(type);
    if (device) return ggml_backend_dev_init(device, nullptr);
    if (ggml_backend_buft_is_host(type)) return ggml_backend_cpu_init();
    throw std::runtime_error("Native head buffer has no supported backend device");
}

struct Cancelled : std::runtime_error {
    Cancelled() : std::runtime_error("Native TwinCore cancelled") {}
};

inline void check_cancelled(const std::atomic<bool> * flag) {
    if (flag && flag->load(std::memory_order_relaxed)) throw Cancelled();
}

inline int retained_prefix(const int * old_ids, int old_count,
                           const int * new_ids, int new_count, bool recompute) {
    if (recompute || old_count < 1 || new_count < 1) return 0;
    // The previous last embedding carried feedback. The new last embedding
    // must be evaluated with the current feedback even for identical IDs.
    const int limit = std::min(old_count - 1, new_count - 1);
    int common = 0;
    while (common < limit && old_ids[common] == new_ids[common]) ++common;
    return common;
}

struct GraphScratch {
    ggml_context * ctx = nullptr;
    ggml_backend_buffer_t buffer = nullptr;
    ggml_cgraph * graph = nullptr;
    explicit GraphScratch() {
        const size_t bytes = ggml_tensor_overhead() * 64 + ggml_graph_overhead_custom(128, false);
        ctx = ggml_init({bytes, nullptr, true});
        if (!ctx) throw std::runtime_error("Head graph metadata allocation failed");
    }
    ~GraphScratch() { if (buffer) ggml_backend_buffer_free(buffer); if (ctx) ggml_free(ctx); }
    GraphScratch(const GraphScratch &) = delete;
    void allocate(ggml_backend_t backend, ggml_tensor * result) {
        graph = ggml_new_graph_custom(ctx, 128, false);
        ggml_build_forward_expand(graph, result);
        buffer = ggml_backend_alloc_ctx_tensors(ctx, backend);
        if (!buffer) throw std::runtime_error("Head scratch allocation failed");
    }
    void compute(ggml_backend_t backend) {
        if (ggml_backend_graph_compute(backend, graph) != GGML_STATUS_SUCCESS)
            throw std::runtime_error("Native head projection failed");
    }
};

inline void project_head(ggml_tensor * weights, ggml_backend_t backend,
                         const float * input, float * output, bool transpose,
                         int chunk_rows = 4096, const std::atomic<bool> * cancelled = nullptr) {
    if (!weights || !weights->buffer || !backend || !input || !output || chunk_rows < 1
        || weights->ne[0] < 1 || weights->ne[1] < 1 || weights->ne[2] != 1 || weights->ne[3] != 1)
        throw std::runtime_error("Invalid native head projection geometry");
    const int width = (int)weights->ne[0], vocab = (int)weights->ne[1];
    for (int i = 0; i < (transpose ? vocab : width); ++i)
        if (!std::isfinite(input[i])) throw std::runtime_error("Nonfinite head input");
    if (transpose) std::fill(output, output + width, 0.0f);
    std::vector<float> partial(width);
    for (int offset = 0; offset < vocab; offset += chunk_rows) {
            check_cancelled(cancelled);
            const int count = std::min(chunk_rows, vocab - offset);
            GraphScratch scratch;
            // Dequantize only this bounded block on the head's own backend.
            // get_rows supports Q6_K without keeping a dense full-head copy.
            auto * slice = ggml_view_2d(scratch.ctx, weights, width, count,
                weights->nb[1], (size_t)offset * weights->nb[1]);
            auto * ids = ggml_new_tensor_1d(scratch.ctx, GGML_TYPE_I32, count);
            auto * rows = ggml_get_rows(scratch.ctx, slice, ids);
            // Q6_K's fast dot kernel quantizes activations to Q8_K. For the
            // adapter operation, use the same frozen dequantized rows in both
            // directions so backward is the derivative of forward.
            auto * matrix = transpose ? ggml_cont(scratch.ctx, ggml_transpose(scratch.ctx, rows)) : rows;
            auto * vector = ggml_new_tensor_2d(scratch.ctx, GGML_TYPE_F32, transpose ? count : width, 1);
            auto * result = ggml_mul_mat(scratch.ctx, matrix, vector);
            scratch.allocate(backend, result);
            std::vector<int> indices(count);
            std::iota(indices.begin(), indices.end(), 0);
            ggml_backend_tensor_set(ids, indices.data(), 0, count * sizeof(int));
            ggml_backend_tensor_set(vector, input + (transpose ? offset : 0), 0,
                (transpose ? count : width) * sizeof(float));
            scratch.compute(backend);
            if (transpose) {
                ggml_backend_tensor_get(result, partial.data(), 0, width * sizeof(float));
                for (int i = 0; i < width; ++i) output[i] += partial[i];
            } else ggml_backend_tensor_get(result, output + offset, 0, count * sizeof(float));
    }
    check_cancelled(cancelled);
    for (int i = 0; i < (transpose ? width : vocab); ++i)
        if (!std::isfinite(output[i])) throw std::runtime_error("Nonfinite head projection");
}
} // namespace twincore
