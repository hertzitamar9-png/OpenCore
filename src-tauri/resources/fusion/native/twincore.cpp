// ABI: MBZUAI-IFM/llama.cpp 42adf019f76013dac873b5b43950d54d5ab27216.
// Complete Nanbeige and K2 towers; Python owns the trainable bridge and one stream.
#include "llama.h"
#include "llama-model.h"
#include "ggml-cpu.h"
#include "head_projection.h"
#include <cstring>
#include <cstdio>
#include <filesystem>
#include <limits>
#include <map>
#include <mutex>
#include <string>

#define TC_API extern "C" __declspec(dllexport)
static thread_local std::string last_error;
static std::once_flag backend_started;

struct Tower {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    ggml_backend_t head_backend = nullptr;
    int width = 0, vocab_size = 0, capacity = 0;
    std::vector<int> prefix;
    std::vector<float> hidden, logits, head_scale;
    std::string callback_error;
    const std::atomic<bool> * cancelled = nullptr;
    ~Tower() {
        if (head_backend) ggml_backend_free(head_backend);
        if (ctx) llama_free(ctx);
        if (model) llama_model_free(model);
    }
    Tower() = default;
    Tower(const Tower &) = delete;
    const llama_vocab * vocab() const { return llama_model_get_vocab(model); }
    void validate_token(int token) const {
        if (token < 0 || token >= vocab_size) throw std::runtime_error("Token exceeds native vocabulary");
    }
    static bool observe(ggml_tensor * tensor, bool ask, void * user) {
        if (std::strcmp(tensor->name, "result_norm")) return !ask;
        if (ask) return true;
        auto & self = *static_cast<Tower *>(user);
        try {
            twincore::capture_last_hidden(tensor, self.width, self.hidden);
        } catch (const std::exception & error) {
            self.callback_error = error.what();
            return false;
        }
        return true;
    }
    void load(const char * path, int context, int layers) {
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = layers;
        mp.split_mode = LLAMA_SPLIT_MODE_NONE; mp.main_gpu = 0;
        model = llama_model_load_from_file(path, mp);
        if (!model) throw std::runtime_error("Could not load complete TwinCore checkpoint");
        width = llama_model_n_embd(model);
        vocab_size = llama_vocab_n_tokens(vocab());
        if (width != llama_model_n_embd_inp(model) || width != llama_model_n_embd_out(model)
            || !model->output || model->output->ne[0] != width || model->output->ne[1] != vocab_size)
            throw std::runtime_error("Unsupported full checkpoint head/embedding geometry");
        auto cp = llama_context_default_params();
        cp.n_ctx = context; cp.n_batch = 256; cp.n_ubatch = 128; cp.n_seq_max = 1;
        cp.n_threads = 2; cp.n_threads_batch = 2;
        cp.embeddings = false; cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.cb_eval = observe; cp.cb_eval_user_data = this;
        // The pinned decoder's abort callback interrupts CPU kernels. CUDA
        // cancellation is additionally checked between native decode batches.
        cp.abort_callback = [](void * data) {
            return static_cast<const std::atomic<bool> *>(data)->load(std::memory_order_relaxed);
        };
        cp.abort_callback_data = const_cast<std::atomic<bool> *>(cancelled);
        cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        cp.type_k = GGML_TYPE_F16; cp.type_v = GGML_TYPE_F16;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) throw std::runtime_error("Could not allocate full TwinCore decoder context");
        capacity = llama_n_ctx(ctx);
        head_backend = twincore::open_head_backend(model->output);
        if (!head_backend) throw std::runtime_error("Could not open the existing native head backend");
        if (ggml_backend_is_cpu(head_backend)) ggml_backend_cpu_set_n_threads(head_backend, 2);
        if (model->output_s) {
            auto * scale = model->output_s;
            const int64_t count = ggml_nelements(scale);
            if (scale->type != GGML_TYPE_F32 || (count != 1 && count != vocab_size))
                throw std::runtime_error("Unsupported native output scaling geometry");
            head_scale.resize((size_t)count);
            ggml_backend_tensor_get(scale, head_scale.data(), 0, count * sizeof(float));
        }
    }
    void clear() {
        llama_memory_clear(llama_get_memory(ctx), true);
        prefix.clear(); hidden.clear(); logits.clear(); callback_error.clear();
    }
    void decode(llama_batch batch) {
        twincore::check_cancelled(cancelled);
        hidden.clear(); callback_error.clear();
        const int status = llama_decode(ctx, batch);
        twincore::check_cancelled(cancelled);
        if (!callback_error.empty()) throw std::runtime_error(callback_error);
        if (status) throw std::runtime_error("Native full decoder failed: " + std::to_string(status));
        if ((int)hidden.size() != width) throw std::runtime_error("Native final hidden state was not observed");
        const auto * values = llama_get_logits_ith(ctx, -1);
        if (!values) throw std::runtime_error("Native full prediction head did not return logits");
        logits.assign(values, values + vocab_size);
        for (float value : hidden) if (!std::isfinite(value)) throw std::runtime_error("Nonfinite decoder feature");
        for (float value : logits) if (!std::isfinite(value)) throw std::runtime_error("Nonfinite decoder logits");
    }
    std::vector<float> embedding(int token) {
        validate_token(token);
        auto * table = model->tok_embd;
        if (!table || table->ne[0] != width || table->ne[1] != vocab_size)
            throw std::runtime_error("Invalid native input embedding geometry");
        const size_t bytes = ggml_row_size(table->type, width);
        std::vector<char> packed(bytes);
        std::vector<float> result(width);
        ggml_backend_tensor_get(table, packed.data(), (size_t)token * table->nb[1], bytes);
        const auto * traits = ggml_get_type_traits(table->type);
        if (table->type == GGML_TYPE_F32) std::memcpy(result.data(), packed.data(), bytes);
        else if (traits->to_float) traits->to_float(packed.data(), result.data(), width);
        else throw std::runtime_error("Unsupported native input embedding type");
        return result;
    }
    void step(const int * ids, int count, const float * feedback, int feedback_count, bool recompute) {
        if (!ids || count < 1 || count > capacity || (feedback_count != 0 && feedback_count != width)
            || (feedback_count && !feedback)) throw std::runtime_error("Invalid native step geometry or finite context capacity");
        for (int i = 0; i < count; ++i) validate_token(ids[i]);
        for (int i = 0; i < feedback_count; ++i)
            if (!std::isfinite(feedback[i])) throw std::runtime_error("Nonfinite decoder feedback");
        int retained = twincore::retained_prefix(prefix.data(), (int)prefix.size(), ids, count, recompute);
        if (!retained || !llama_memory_seq_rm(llama_get_memory(ctx), 0, retained, -1)) {
            clear(); retained = 0;
        }
        for (int offset = retained; offset < count - 1; offset += 256) {
            const int n = std::min(256, count - 1 - offset);
            auto batch = llama_batch_init(n, 0, 1);
            batch.n_tokens = n;
            for (int i = 0; i < n; ++i) {
                batch.token[i] = ids[offset + i]; batch.pos[i] = offset + i;
                batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0; batch.logits[i] = i == n - 1;
            }
            try { decode(batch); } catch (...) { llama_batch_free(batch); throw; }
            llama_batch_free(batch);
        }
        auto input = embedding(ids[count - 1]);
        if (feedback_count) for (int i = 0; i < width; ++i) input[i] += feedback[i];
        auto batch = llama_batch_init(1, width, 1);
        batch.n_tokens = 1;
        std::memcpy(batch.embd, input.data(), width * sizeof(float));
        batch.pos[0] = count - 1; batch.n_seq_id[0] = 1; batch.seq_id[0][0] = 0; batch.logits[0] = 1;
        try { decode(batch); } catch (...) { llama_batch_free(batch); throw; }
        llama_batch_free(batch);
        prefix.assign(ids, ids + count);
    }
    void project(const float * input, float * output, bool transpose) {
        std::vector<float> scaled;
        if (transpose && !head_scale.empty()) {
            scaled.assign(input, input + vocab_size);
            for (int i = 0; i < vocab_size; ++i) scaled[i] *= head_scale[head_scale.size() == 1 ? 0 : i];
            input = scaled.data();
        }
        twincore::project_head(model->output, head_backend, input, output, transpose, 4096, cancelled);
        if (!transpose && !head_scale.empty())
            for (int i = 0; i < vocab_size; ++i) output[i] *= head_scale[head_scale.size() == 1 ? 0 : i];
    }
};

struct TwinCore {
    // Declared first so it outlives both towers during destruction.
    std::atomic<bool> cancelled{false};
    Tower nanbeige, k2;
    bool recompute = false;
};
static Tower & tower(void * handle, int brain) {
    if (!handle || brain < 0 || brain > 1) throw std::runtime_error("Invalid native TwinCore handle or brain");
    return brain ? static_cast<TwinCore *>(handle)->k2 : static_cast<TwinCore *>(handle)->nanbeige;
}
TC_API const char * tc_error() { return last_error.c_str(); }
TC_API int tc_abi() { return 1; }
TC_API void * tc_create(const char * nanbeige, const char * k2, int context, int layers, int recompute) {
    try {
        if (!nanbeige || !k2 || !std::filesystem::is_regular_file(std::filesystem::u8path(nanbeige))
            || !std::filesystem::is_regular_file(std::filesystem::u8path(k2)))
            throw std::runtime_error("Both complete checkpoint files are required");
        if (context < 32 || context > 8192 || layers < 0) throw std::runtime_error("Invalid experimental context range or GPU layer count");
        std::call_once(backend_started, [] {
            llama_log_set([](ggml_log_level level, const char * text, void *) {
                if (level >= GGML_LOG_LEVEL_WARN) std::fputs(text, stderr);
            }, nullptr);
            llama_backend_init();
        });
        auto model = std::make_unique<TwinCore>();
        model->nanbeige.cancelled = model->k2.cancelled = &model->cancelled;
        model->nanbeige.load(nanbeige, context, layers);
        model->k2.load(k2, context, layers);
        model->recompute = recompute != 0;
        return model.release();
    } catch (const std::exception & error) { last_error = error.what(); return nullptr; }
}
TC_API void tc_destroy(void * handle) { delete static_cast<TwinCore *>(handle); }
TC_API int tc_geometry(void * handle, int brain, int field) {
    try {
        auto & t = tower(handle, brain);
        if (field == 0) return t.width;
        if (field == 1) return t.vocab_size;
        if (field == 2) return t.capacity;
        throw std::runtime_error("Invalid native geometry field");
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API uint64_t tc_parameters(void * handle, int brain) {
    try { return llama_model_n_params(tower(handle, brain).model); }
    catch (const std::exception & error) { last_error = error.what(); return 0; }
}
TC_API int tc_placement(void * handle, int brain, int field) {
    try {
        auto & t = tower(handle, brain);
        if (field == 3) return llama_model_n_layer(t.model);
        // The pinned DLL does not export its private dev_layer() method. Inspect
        // actual decoder matrix buffers instead of trusting requested offload.
        // Recurrent Nanbeige shares physical matrices across logical loop slots.
        std::map<int, bool> placement;
        for (const auto & item : t.model->tensors_by_name) {
            const auto & name = item.first;
            auto * tensor = item.second;
            if (name.rfind("blk.", 0) != 0 || tensor->ne[1] < 2) continue;
            const size_t end = name.find('.', 4);
            if (end == std::string::npos) throw std::runtime_error("Invalid decoder tensor name");
            const int layer = std::stoi(name.substr(4, end - 4));
            const bool gpu = twincore::tensor_on_gpu(tensor);
            auto inserted = placement.emplace(layer, true);
            inserted.first->second = inserted.first->second && gpu;
        }
        if (field == 0) return (int)placement.size();
        if (field == 1) return (int)std::count_if(placement.begin(), placement.end(),
            [](const auto & item) { return item.second; });
        if (field == 2)
            return ggml_backend_dev_type(ggml_backend_get_device(t.head_backend)) == GGML_BACKEND_DEVICE_TYPE_GPU;
        throw std::runtime_error("Invalid native placement field");
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_step(void * handle, const int * n_ids, int n_count, const int * k_ids, int k_count,
                   const float * n_bias, int n_bias_count, const float * k_bias, int k_bias_count) {
    try {
        auto & n = tower(handle, 0); auto & k = tower(handle, 1);
        const bool recompute = static_cast<TwinCore *>(handle)->recompute;
        n.step(n_ids, n_count, n_bias, n_bias_count, recompute);
        k.step(k_ids, k_count, k_bias, k_bias_count, recompute);
        return 0;
    } catch (const twincore::Cancelled & error) { last_error = error.what(); return -2; }
      catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_read(void * handle, int brain, float * hidden, int h_count, float * logits, int v_count) {
    try {
        auto & t = tower(handle, brain);
        if (!hidden || !logits || h_count != t.width || v_count != t.vocab_size
            || (int)t.hidden.size() != h_count || (int)t.logits.size() != v_count)
            throw std::runtime_error("Native features unavailable or wrong read geometry");
        std::copy(t.hidden.begin(), t.hidden.end(), hidden);
        std::copy(t.logits.begin(), t.logits.end(), logits);
        return 0;
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_project(void * handle, int brain, const float * input, int input_count,
                      float * output, int output_count, int transpose) {
    try {
        auto & t = tower(handle, brain);
        if (!input || !output || input_count != (transpose ? t.vocab_size : t.width)
            || output_count != (transpose ? t.width : t.vocab_size)) throw std::runtime_error("Invalid head vector geometry");
        t.project(input, output, transpose != 0);
        return 0;
    } catch (const twincore::Cancelled & error) { last_error = error.what(); return -2; }
      catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_cancel(void * handle) {
    if (!handle) { last_error = "Invalid cancellation handle"; return -1; }
    static_cast<TwinCore *>(handle)->cancelled.store(true, std::memory_order_relaxed);
    return 0;
}
TC_API int tc_clear(void * handle) {
    try {
        auto & n = tower(handle, 0); auto & k = tower(handle, 1);
        n.clear(); k.clear();
        static_cast<TwinCore *>(handle)->cancelled.store(false, std::memory_order_relaxed);
        return 0;
    }
    catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_tokenize(void * handle, int brain, const char * text, int length,
                       int special, int * output, int capacity) {
    try {
        auto & t = tower(handle, brain);
        if (!text || length < 0 || capacity < 0) throw std::runtime_error("Invalid tokenization buffer");
        const int result = llama_tokenize(t.vocab(), text, length, output, capacity, special != 0, true);
        if (result == std::numeric_limits<int>::min()) throw std::runtime_error("Native tokenization overflow");
        return result < 0 ? -result : result;
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_piece(void * handle, int brain, int token, char * output, int capacity) {
    try {
        auto & t = tower(handle, brain); t.validate_token(token);
        if (capacity < 0) throw std::runtime_error("Invalid piece buffer");
        const int result = llama_token_to_piece(t.vocab(), token, output, capacity, 0, false);
        return result < 0 ? -result : result;
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_token_flags(void * handle, int brain, int token) {
    try {
        auto & t = tower(handle, brain); t.validate_token(token);
        const auto attr = llama_vocab_get_attr(t.vocab(), token);
        return (llama_vocab_is_eog(t.vocab(), token) ? 1 : 0)
            | (attr & (LLAMA_TOKEN_ATTR_CONTROL | LLAMA_TOKEN_ATTR_UNKNOWN | LLAMA_TOKEN_ATTR_UNUSED) ? 2 : 0);
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_chat_data(void * handle, int brain, int field, char * output, int capacity) {
    try {
        auto & t = tower(handle, brain);
        if (capacity < 0 || (capacity && !output)) throw std::runtime_error("Invalid chat metadata output");
        std::string value;
        if (field == 0) {
            const char * source = llama_model_chat_template(t.model, nullptr);
            if (!source || !*source) throw std::runtime_error("Checkpoint has no stored chat template");
            value = source;
        } else if (field == 1 || field == 2) {
            const int token = field == 1 ? llama_vocab_bos(t.vocab()) : llama_vocab_eos(t.vocab());
            if (token != LLAMA_TOKEN_NULL) {
                const int needed = llama_token_to_piece(t.vocab(), token, nullptr, 0, 0, true);
                value.resize((size_t)std::abs(needed));
                const int written = llama_token_to_piece(t.vocab(), token, value.data(), (int)value.size(), 0, true);
                if (written != (int)value.size()) throw std::runtime_error("Special token metadata changed");
            }
        } else throw std::runtime_error("Invalid chat metadata field");
        if (value.size() > (size_t)std::numeric_limits<int>::max()) throw std::runtime_error("Chat metadata is too large");
        if (capacity) {
            if (capacity < (int)value.size()) throw std::runtime_error("Chat metadata buffer is too small");
            std::memcpy(output, value.data(), value.size());
        }
        return (int)value.size();
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
TC_API int tc_format(void * handle, int brain, const char ** roles, const char ** contents,
                     int count, char * output, int capacity) {
    try {
        auto & t = tower(handle, brain);
        if (!roles || !contents || count < 1 || capacity < 0) throw std::runtime_error("Invalid chat template input");
        std::vector<llama_chat_message> messages;
        for (int i = 0; i < count; ++i) {
            if (!roles[i] || !contents[i]) throw std::runtime_error("Missing chat message");
            messages.push_back({roles[i], contents[i]});
        }
        const int result = llama_chat_apply_template(llama_model_chat_template(t.model, nullptr),
            messages.data(), messages.size(), true, output, capacity);
        if (result < 0) throw std::runtime_error("Native checkpoint chat template is unsupported");
        return result;
    } catch (const std::exception & error) { last_error = error.what(); return -1; }
}
