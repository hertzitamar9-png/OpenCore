// CPU-only test utility. It exercises the same head math as the real backend.
#include "head_projection.h"
#include "ggml-cpu.h"
#include <cstring>
#include <string>

#define TP_API extern "C" __declspec(dllexport)
static thread_local std::string probe_error;
struct Probe {
    ggml_context * ctx = nullptr;
    ggml_backend_t backend = nullptr;
    ggml_backend_buffer_t buffer = nullptr;
    ggml_tensor * weights = nullptr;
    std::vector<char> mapped_storage;
    int chunk = 0;
    ~Probe() {
        if (buffer) ggml_backend_buffer_free(buffer);
        if (ctx) ggml_free(ctx);
        if (backend) ggml_backend_free(backend);
    }
};
TP_API const char * tp_error() { return probe_error.c_str(); }
static void * create_probe(int width, int vocab, int quantized, int chunk, bool mapped, bool gpu = false) {
    try {
        auto probe = std::make_unique<Probe>();
        if (gpu) {
            auto * device = ggml_backend_dev_by_type(GGML_BACKEND_DEVICE_TYPE_GPU);
            if (!device) throw std::runtime_error("Native CUDA backend is not registered");
            probe->backend = ggml_backend_dev_init(device, nullptr);
        } else probe->backend = ggml_backend_cpu_init();
        if (!probe->backend) throw std::runtime_error("Native head fixture backend unavailable");
        if (ggml_backend_is_cpu(probe->backend)) ggml_backend_cpu_set_n_threads(probe->backend, 2);
        probe->ctx = ggml_init({ggml_tensor_overhead() * 4, nullptr, true});
        probe->weights = ggml_new_tensor_2d(probe->ctx, quantized ? GGML_TYPE_Q6_K : GGML_TYPE_F32, width, vocab);
        if (mapped) {
            probe->mapped_storage.resize(ggml_nbytes(probe->weights) + 64);
            auto * address = (void *)(((uintptr_t)probe->mapped_storage.data() + 63) & ~(uintptr_t)63);
            probe->buffer = ggml_backend_cpu_buffer_from_ptr(address, ggml_nbytes(probe->weights));
            if (!probe->buffer || ggml_backend_tensor_alloc(probe->buffer, probe->weights, address) != GGML_STATUS_SUCCESS)
                throw std::runtime_error("CPU mapped weight fixture allocation failed");
            ggml_backend_free(probe->backend);
            probe->backend = twincore::open_head_backend(probe->weights);
            if (!probe->backend) throw std::runtime_error("CPU mapped head backend unavailable");
            ggml_backend_cpu_set_n_threads(probe->backend, 2);
        } else probe->buffer = ggml_backend_alloc_ctx_tensors(probe->ctx, probe->backend);
        if (!probe->buffer) throw std::runtime_error("CPU weight fixture allocation failed");
        std::vector<float> values((size_t)width * vocab);
        for (size_t i = 0; i < values.size(); ++i) values[i] = 0.125f * std::sin((float)i * 0.013f);
        if (quantized) {
            std::vector<char> packed(ggml_nbytes(probe->weights));
            const auto bytes = ggml_quantize_chunk(GGML_TYPE_Q6_K, values.data(), packed.data(), 0, vocab, width, nullptr);
            ggml_backend_tensor_set(probe->weights, packed.data(), 0, bytes);
        } else ggml_backend_tensor_set(probe->weights, values.data(), 0, values.size() * sizeof(float));
        probe->chunk = chunk;
        return probe.release();
    } catch (const std::exception & error) { probe_error = error.what(); return nullptr; }
}
TP_API void * tp_create(int width, int vocab, int quantized, int chunk) {
    return create_probe(width, vocab, quantized, chunk, false);
}
TP_API void * tp_create_mapped(int width, int vocab, int quantized, int chunk) {
    return create_probe(width, vocab, quantized, chunk, true);
}
TP_API void * tp_create_gpu(int width, int vocab, int quantized, int chunk) {
    return create_probe(width, vocab, quantized, chunk, false, true);
}
TP_API void * tp_buffer_device(void * handle) {
    auto & p = *static_cast<Probe *>(handle);
    return ggml_backend_buft_get_device(ggml_backend_buffer_get_type(p.buffer));
}
TP_API int tp_on_gpu(void * handle) {
    return twincore::tensor_on_gpu(static_cast<Probe *>(handle)->weights) ? 1 : 0;
}
TP_API void tp_destroy(void * handle) { delete static_cast<Probe *>(handle); }
TP_API int tp_project(void * handle, const float * input, float * output, int transpose) {
    try {
        auto & p = *static_cast<Probe *>(handle);
        twincore::project_head(p.weights, p.backend, input, output, transpose != 0, p.chunk);
        return 0;
    } catch (const std::exception & error) { probe_error = error.what(); return -1; }
}
TP_API int tp_weights(void * handle, float * output) {
    auto & p = *static_cast<Probe *>(handle);
    const size_t row_bytes = ggml_row_size(p.weights->type, p.weights->ne[0]);
    std::vector<char> packed(row_bytes);
    const auto * traits = ggml_get_type_traits(p.weights->type);
    for (int i = 0; i < p.weights->ne[1]; ++i) {
        ggml_backend_tensor_get(p.weights, packed.data(), i * row_bytes, row_bytes);
        if (p.weights->type == GGML_TYPE_F32) std::memcpy(output + i * p.weights->ne[0], packed.data(), row_bytes);
        else traits->to_float(packed.data(), output + i * p.weights->ne[0], p.weights->ne[0]);
    }
    return 0;
}
TP_API int tp_retain(const int * old_ids, int old_count, const int * new_ids, int new_count, int recompute) {
    return twincore::retained_prefix(old_ids, old_count, new_ids, new_count, recompute != 0);
}
TP_API int tp_capture_last_hidden(void * handle, int rows, int width, float * output) {
    try {
        auto & p = *static_cast<Probe *>(handle);
        if (!output || rows < 0 || rows > p.weights->ne[1]) throw std::runtime_error("Invalid hidden fixture rows");
        auto current = *p.weights;
        current.ne[1] = rows;
        std::vector<float> hidden;
        if (!twincore::capture_last_hidden(&current, width, hidden)) return 0;
        std::memcpy(output, hidden.data(), hidden.size() * sizeof(float));
        return 1;
    } catch (const std::exception & error) { probe_error = error.what(); return -1; }
}
