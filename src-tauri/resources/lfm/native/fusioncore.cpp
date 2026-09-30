// Pinned to MBZUAI-IFM/llama.cpp 42adf019f76013dac873b5b43950d54d5ab27216.
// Two complete weight sets, bidirectional hidden feedback, one sampled token stream.
// Coupling is deterministic and untuned: no claim of trained quality improvement.
#include "llama.h"
#include "llama-model.h"
#include "ggml-backend.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#define FC_API extern "C" __declspec(dllexport)
static thread_local std::string last_error;

struct Tower {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    std::vector<llama_token> prefix;
    std::vector<std::vector<float>> generated_inputs;
    int position = 0;
    int hidden_size = 0;
    int capacity = 0;
    std::vector<float> hidden;
    llama_sampler * grammar = nullptr;
    bool done = false;
    ~Tower() { if (grammar) llama_sampler_free(grammar); if (ctx) llama_free(ctx); if (model) llama_model_free(model); }

    static bool observe(ggml_tensor * tensor, bool ask, void * user) {
        if (std::strcmp(tensor->name, "result_norm") != 0) return ask ? false : true;
        if (ask) return true;
        auto & tower = *static_cast<Tower *>(user);
        if (tensor->type != GGML_TYPE_F32 || tensor->ne[0] != tower.hidden_size || tensor->ne[1] < 1)
            return false;
        tower.hidden.resize(tower.hidden_size);
        ggml_backend_tensor_get(tensor, tower.hidden.data(),
            (size_t)(tensor->ne[1] - 1) * tensor->nb[1], tower.hidden_size * sizeof(float));
        return true;
    }

    void load(const char * path, int context, int layers) {
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = layers;
        model = llama_model_load_from_file(path, mp);
        if (!model) throw std::runtime_error("Could not load full LFM checkpoint");
        hidden_size = llama_model_n_embd(model);
        auto cp = llama_context_default_params();
        cp.n_ctx = context; cp.n_batch = 512; cp.n_ubatch = 128; cp.n_seq_max = 1;
        cp.n_threads = 4; cp.n_threads_batch = 4;
        // In the pinned LFM graph, embedding mode skips the prediction head.
        // Observe the final normalized state while keeping token generation enabled.
        cp.embeddings = false; cp.pooling_type = LLAMA_POOLING_TYPE_NONE;
        cp.cb_eval = observe; cp.cb_eval_user_data = this;
        cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        cp.type_k = GGML_TYPE_F16; cp.type_v = GGML_TYPE_F16;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) throw std::runtime_error("Could not allocate FusionCore context");
        capacity = llama_n_ctx(ctx);
    }
    std::vector<llama_token> tokenize(const std::string & text, bool special) {
        const auto * vocab = llama_model_get_vocab(model);
        int count = -llama_tokenize(vocab, text.data(), (int)text.size(), nullptr, 0, special, true);
        if (count < 0) throw std::runtime_error("Invalid tokenized prompt");
        std::vector<llama_token> ids(count);
        count = llama_tokenize(vocab, text.data(), (int)text.size(), ids.data(), count, special, true);
        if (count < 0) throw std::runtime_error("Tokenization failed");
        ids.resize(count); return ids;
    }
    void clear() { llama_memory_clear(llama_get_memory(ctx), true); position = 0; }
    void tokens(const std::vector<llama_token> & ids) {
        for (size_t offset=0; offset<ids.size(); offset+=512) {
            const int count = (int)std::min<size_t>(512, ids.size()-offset);
            if (position+count > capacity) throw std::runtime_error("FusionCore active context is full");
            auto batch = llama_batch_init(count, 0, 1);
            batch.n_tokens = count;
            for (int i=0; i<count; ++i) {
                batch.token[i] = ids[offset+i]; batch.pos[i] = position+i;
                batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0; batch.logits[i] = i == count-1;
            }
            hidden.clear();
            const int result = llama_decode(ctx, batch);
            llama_batch_free(batch);
            if (result) throw std::runtime_error("LFM token decoding failed: " + std::to_string(result));
            if (hidden.empty()) throw std::runtime_error("Final LFM hidden state was not observed");
            position += count;
        }
    }
    std::vector<float> token_embedding(int token) {
        const auto * table = model->tok_embd;
        if (!table || table->ne[0] != hidden_size || token < 0 || token >= table->ne[1])
            throw std::runtime_error("Invalid native input embedding geometry");
        const size_t bytes = ggml_row_size(table->type, hidden_size);
        std::vector<char> packed(bytes);
        std::vector<float> value(hidden_size);
        ggml_backend_tensor_get(table, packed.data(), (size_t)token * bytes, bytes);
        const auto * traits = ggml_get_type_traits(table->type);
        if (table->type == GGML_TYPE_F32) std::memcpy(value.data(), packed.data(), bytes);
        else if (traits->to_float) traits->to_float(packed.data(), value.data(), hidden_size);
        else throw std::runtime_error("Unsupported input embedding type");
        return value;
    }
    void embeddings(const std::vector<std::vector<float>> & rows) {
        for (size_t offset=0; offset<rows.size(); offset+=512) {
            const int count = (int)std::min<size_t>(512, rows.size()-offset);
            if (position+count > capacity) throw std::runtime_error("FusionCore active context is full");
            auto batch = llama_batch_init(count, hidden_size, 1);
            batch.n_tokens = count;
            for (int i=0; i<count; ++i) {
                std::memcpy(batch.embd + (size_t)i*hidden_size, rows[offset+i].data(), hidden_size*sizeof(float));
                batch.pos[i] = position+i; batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0;
                batch.logits[i] = i == count-1;
            }
            hidden.clear();
            const int result = llama_decode(ctx, batch); llama_batch_free(batch);
            if (result) throw std::runtime_error("Coupled embedding decoding failed: " + std::to_string(result));
            if (hidden.empty()) throw std::runtime_error("Final coupled hidden state was not observed");
            position += count;
        }
    }
    void start(const char * text) {
        done = false; clear(); generated_inputs.clear(); prefix = tokenize(text, true); tokens(prefix);
        if (grammar) { llama_sampler_free(grammar); grammar = nullptr; }
    }
    std::vector<float> coupled_input(int token, const std::vector<float> & peer, float strength) {
        auto value = token_embedding(token);
        double value_power=0, peer_power=0;
        for (int i=0; i<hidden_size; ++i) { value_power += value[i]*value[i]; peer_power += peer[i]*peer[i]; }
        const float scale = strength * (float)std::sqrt(value_power / std::max(peer_power, 1e-12));
        for (int i=0; i<hidden_size; ++i) value[i] += scale*peer[i];
        return value;
    }
    void advance(std::vector<float> input, bool recompute) {
        if (recompute) {
            generated_inputs.push_back(std::move(input));
            clear(); tokens(prefix); embeddings(generated_inputs);
        } else embeddings({input});
    }
};

struct FusionCore {
    Tower left, right;
    bool recompute = false;
    float coupling = 0.02f;
    int vocab_size = 0;
    bool done = false;
    bool reasoning_active = false;
    int reasoning_budget = -1;
    int reasoning_tokens = 0;
    int reasoning_end_position = -1;
    std::vector<llama_token> reasoning_end_tokens;
    std::vector<llama_token> generated_tokens;
};

FC_API const char * fc_error() { return last_error.c_str(); }
FC_API void * fc_create(const char * path, int context, int layers, int recompute) {
    try {
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level >= GGML_LOG_LEVEL_WARN) std::fputs(text, stderr);
        }, nullptr);
        llama_backend_init();
        auto model = std::make_unique<FusionCore>();
        model->left.load(path, context, layers); model->right.load(path, context, layers);
        if (model->left.hidden_size != model->right.hidden_size) throw std::runtime_error("Incompatible tower geometry");
        model->vocab_size = llama_vocab_n_tokens(llama_model_get_vocab(model->left.model));
        model->recompute = recompute != 0;
        return model.release();
    } catch (const std::exception & error) { last_error=error.what(); return nullptr; }
}
FC_API void fc_destroy(void * handle) { delete static_cast<FusionCore *>(handle); }
FC_API void fc_clear_brain(void * handle, int brain) {
    auto & model = *static_cast<FusionCore *>(handle);
    auto & tower = brain == 0 ? model.left : model.right;
    tower.clear(); tower.prefix.clear(); tower.generated_inputs.clear();
}
FC_API int fc_format(void * handle, const char ** roles, const char ** contents, int count, char * buffer, int capacity) {
    try {
        auto & tower = static_cast<FusionCore *>(handle)->left;
        std::vector<llama_chat_message> messages;
        for (int i=0; i<count; ++i) messages.push_back({roles[i], contents[i]});
        const int result = llama_chat_apply_template(llama_model_chat_template(tower.model, nullptr),
            messages.data(), messages.size(), true, buffer, capacity);
        if (result < 0) throw std::runtime_error("Checkpoint chat template is unsupported by pinned runtime");
        return result;
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}
FC_API int fc_start(void * handle, const char * left, const char * right, int reasoning_budget) {
    try {
        auto & model = *static_cast<FusionCore *>(handle);
        if (reasoning_budget < -1) throw std::runtime_error("Reasoning budget must be -1 or non-negative");
        model.reasoning_budget = reasoning_budget;
        model.reasoning_tokens = 0;
        model.reasoning_active = reasoning_budget >= 0;
        model.reasoning_end_position = -1;
        model.reasoning_end_tokens.clear();
        model.generated_tokens.clear();
        if (model.reasoning_active) {
            model.reasoning_end_tokens = model.left.tokenize("</think>", true);
            if (model.reasoning_end_tokens.empty()) throw std::runtime_error("The tokenizer produced no closing think tokens");
        }
        model.done=false; model.left.start(left); model.right.start(right);
        return std::max(model.left.position, model.right.position);
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}
FC_API int fc_count(void * handle, const char * text) {
    try { return (int)static_cast<FusionCore *>(handle)->left.tokenize(text, false).size(); }
    catch (const std::exception & error) { last_error=error.what(); return -1; }
}
FC_API int fc_tokenize(void * handle, const char * text, int * output, int capacity) {
    try {
        auto ids=static_cast<FusionCore *>(handle)->left.tokenize(text, false);
        if ((int)ids.size()<=capacity && output) std::copy(ids.begin(), ids.end(), output);
        return (int)ids.size();
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}
FC_API int fc_detokenize(void * handle, const int * ids, int count, char * output, int capacity) {
    const auto * vocab=llama_model_get_vocab(static_cast<FusionCore *>(handle)->left.model);
    return llama_detokenize(vocab, ids, count, output, capacity, false, true);
}
FC_API int fc_capacity(void * handle) { return static_cast<FusionCore *>(handle)->left.capacity; }
FC_API unsigned long long fc_parameters(void * handle) {
    auto & model = *static_cast<FusionCore *>(handle);
    return llama_model_n_params(model.left.model) + llama_model_n_params(model.right.model);
}
FC_API int fc_next(void * handle, char * piece, int capacity, int * selected_token) {
    try {
        auto & model = *static_cast<FusionCore *>(handle);
        if (model.done) return 0;
        const float * left = llama_get_logits_ith(model.left.ctx, -1);
        const float * right = llama_get_logits_ith(model.right.ctx, -1);
        if (!left || !right || model.left.hidden.empty() || model.right.hidden.empty())
            throw std::runtime_error("A complete tower did not return logits and hidden state");
        int token=0; float best=-INFINITY;
        if (model.reasoning_active && model.reasoning_tokens >= model.reasoning_budget) {
            model.reasoning_active = false;
            model.reasoning_end_position = 0;
        }
        if (model.reasoning_end_position >= 0) {
            token = model.reasoning_end_tokens[model.reasoning_end_position++];
            if (model.reasoning_end_position >= (int)model.reasoning_end_tokens.size())
                model.reasoning_end_position = -1;
        } else {
            for (int i=0; i<model.vocab_size; ++i) {
                const float score=0.5f*left[i]+0.5f*right[i];
                if (score>best) { best=score; token=i; }
            }
            if (!std::isfinite(best)) throw std::runtime_error("Non-finite fused scores");
        }
        if (model.reasoning_active) {
            ++model.reasoning_tokens;
            model.generated_tokens.push_back(token);
            if (model.generated_tokens.size() >= model.reasoning_end_tokens.size() &&
                std::equal(model.reasoning_end_tokens.rbegin(), model.reasoning_end_tokens.rend(),
                           model.generated_tokens.rbegin()))
                model.reasoning_active = false;
        }
        *selected_token = token;
        const auto * vocab = llama_model_get_vocab(model.left.model);
        if (llama_vocab_is_eog(vocab, token)) { model.done=true; return 0; }
        const int length = llama_token_to_piece(vocab, token, piece, capacity, 0, true);
        if (length < 0) throw std::runtime_error("Token piece buffer too small");
        auto left_hidden = model.left.hidden, right_hidden = model.right.hidden;
        auto linput = model.left.coupled_input(token, right_hidden, model.coupling);
        auto rinput = model.right.coupled_input(token, left_hidden, model.coupling);
        model.left.advance(std::move(linput), model.recompute);
        model.right.advance(std::move(rinput), model.recompute);
        return length + 1; // Zero is reserved for end-of-generation, including empty token pieces.
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}

// DualCore gives each loaded tower its own draft/review stream. Incremental KV
// is the normal path; explicit full-prefix recomputation remains opt-in.
FC_API int fc_brain_start(void * handle, int brain, const char * prompt, int review) {
    try {
        auto & model = *static_cast<FusionCore *>(handle);
        if (brain < 0 || brain > 1) throw std::runtime_error("Invalid brain index");
        auto & tower = brain == 0 ? model.left : model.right;
        tower.start(prompt);
        if (review) {
            static const char * schema = R"(
root ::= "{" ws "\"score_a\"" ws ":" ws number ws "," ws "\"score_b\"" ws ":" ws number ws "," ws "\"confidence\"" ws ":" ws number ws "," ws "\"reason\"" ws ":" ws string ws "}"
number ::= ("100" | [1-9] [0-9]? | "0") ("." [0-9]{1,4})?
string ::= "\"" char{0,180} "\""
char ::= [^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})
ws ::= [ \t\n\r]?
)";
            tower.grammar = llama_sampler_init_grammar(llama_model_get_vocab(tower.model), schema, "root");
            if (!tower.grammar) throw std::runtime_error("Could not initialize native review grammar");
        }
        return tower.position;
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}
FC_API int fc_brain_next(void * handle, int brain, char * piece, int capacity, int * selected_token) {
    try {
        auto & model = *static_cast<FusionCore *>(handle);
        if (brain < 0 || brain > 1) throw std::runtime_error("Invalid brain index");
        auto & tower = brain == 0 ? model.left : model.right;
        if (tower.done) return 0;
        const auto * vocab = llama_model_get_vocab(tower.model);
        const float * logits = llama_get_logits_ith(tower.ctx, -1);
        if (!logits) throw std::runtime_error("Independent tower returned no scores");
        std::vector<llama_token_data> scores(model.vocab_size);
        for (int i=0; i<model.vocab_size; ++i) scores[i] = {i, logits[i], 0};
        llama_token_data_array candidates = {scores.data(), scores.size(), -1, false};
        if (tower.grammar) llama_sampler_apply(tower.grammar, &candidates);
        int token=0; float best=-INFINITY;
        for (const auto & score : scores) if (score.logit>best) { best=score.logit; token=score.id; }
        if (!std::isfinite(best)) throw std::runtime_error("No valid independent token");
        *selected_token=token;
        if (tower.grammar) llama_sampler_accept(tower.grammar, token);
        if (llama_vocab_is_eog(vocab, token)) { tower.done=true; return 0; }
        const int length=llama_token_to_piece(vocab, token, piece, capacity, 0, true);
        if (length<0) throw std::runtime_error("Independent token buffer too small");
        tower.advance(tower.token_embedding(token), model.recompute);
        return length + 1;
    } catch (const std::exception & error) { last_error=error.what(); return -1; }
}
