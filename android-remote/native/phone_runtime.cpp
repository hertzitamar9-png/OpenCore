#include <jni.h>
#include "llama.h"
#include "ggml-backend.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::mutex owner;
std::atomic<bool> stopped{false};
llama_model *model=nullptr;
llama_context *context=nullptr;
std::once_flag initialized;
using Clock=std::chrono::steady_clock;
void fail(JNIEnv *env,const char *message){env->ThrowNew(env->FindClass("java/io/IOException"),message);}
std::string utf8(JNIEnv *env,jstring s){
    jclass cls=env->FindClass("java/lang/String");
    jmethodID method=env->GetMethodID(cls,"getBytes","(Ljava/lang/String;)[B");
    jstring encoding=env->NewStringUTF("UTF-8");
    auto array=(jbyteArray)env->CallObjectMethod(s,method,encoding);
    if(env->ExceptionCheck())throw std::runtime_error("Could not read the model prompt.");
    std::string out(env->GetArrayLength(array),'\0');env->GetByteArrayRegion(array,0,(jsize)out.size(),(jbyte*)out.data());
    env->DeleteLocalRef(encoding);env->DeleteLocalRef(array);env->DeleteLocalRef(cls);return out;
}
bool abort_decode(void*){return stopped.load();}
bool progress(float,void*){return !stopped.load();}
void close_model(){if(context){llama_free(context);context=nullptr;}if(model){llama_model_free(model);model=nullptr;}}
void update(JNIEnv *env,jobject callback,jmethodID method,const std::string &output,int prompt,int generated,Clock::time_point start){
    jbyteArray bytes=env->NewByteArray((jsize)output.size());
    if(!bytes)throw std::runtime_error("Phone memory exhausted while streaming output.");
    env->SetByteArrayRegion(bytes,0,(jsize)output.size(),(const jbyte*)output.data());
    env->CallVoidMethod(callback,method,bytes,prompt,generated,(jlong)std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start).count());
    env->DeleteLocalRef(bytes);
    if(env->ExceptionCheck()){stopped=true;throw std::runtime_error("The phone generation callback closed.");}
}
}

extern "C" JNIEXPORT void JNICALL Java_ai_opencore_remote_PhoneNative_cancel(JNIEnv*,jclass){stopped=true;}
extern "C" JNIEXPORT void JNICALL Java_ai_opencore_remote_PhoneNative_prepare(JNIEnv*,jclass){stopped=false;}
extern "C" JNIEXPORT void JNICALL Java_ai_opencore_remote_PhoneNative_unload(JNIEnv*,jclass){std::lock_guard<std::mutex> lock(owner);close_model();}

extern "C" JNIEXPORT void JNICALL Java_ai_opencore_remote_PhoneNative_load(JNIEnv *env,jclass,jstring path,jint window,jint threads){
    std::lock_guard<std::mutex> lock(owner);
    try{
        if(window<512||window>16384||threads<1||threads>8)throw std::runtime_error("Invalid phone runtime limits.");
        std::call_once(initialized,[]{llama_backend_init();ggml_backend_load_all();});
        close_model();auto params=llama_model_default_params();params.n_gpu_layers=0;
        params.progress_callback=progress;params.progress_callback_user_data=nullptr;
        model=llama_model_load_from_file(utf8(env,path).c_str(),params);
        if(!model)throw std::runtime_error("This GGUF architecture or quantization could not be loaded by the bundled Android runtime. Try another quantization or the paired PC.");
        auto settings=llama_context_default_params();settings.n_ctx=std::min((uint32_t)window,(uint32_t)llama_model_n_ctx_train(model));
        settings.n_batch=256;settings.n_ubatch=128;settings.n_threads=threads;settings.n_threads_batch=threads;
        settings.abort_callback=abort_decode;settings.abort_callback_data=nullptr;
        context=llama_init_from_model(model,settings);
        if(!context)throw std::runtime_error("The phone could not allocate this model's context. Reduce context size or choose a smaller quantization.");
        if(stopped)throw std::runtime_error("Model loading stopped.");
    }catch(const std::exception &error){close_model();if(!env->ExceptionCheck())fail(env,error.what());}
}

extern "C" JNIEXPORT jstring JNICALL Java_ai_opencore_remote_PhoneNative_generate(JNIEnv *env,jclass,jobjectArray roles,jobjectArray contents,jint max_tokens,jfloat temperature,jfloat top_p,jint top_k,jfloat repeat_penalty,jlong seed,jobject callback){
    std::lock_guard<std::mutex> lock(owner);llama_sampler *sampler=nullptr;
    try{
        if(!context||!model)throw std::runtime_error("Load a phone model before sending a message.");
        if(max_tokens<1||max_tokens>32768||!std::isfinite(temperature)||temperature<0||temperature>2||!std::isfinite(top_p)||top_p<.01f||top_p>1||top_k<0||top_k>1000||!std::isfinite(repeat_penalty)||repeat_penalty<.5f||repeat_penalty>2||seed < -1||seed>4294967295LL)throw std::runtime_error("Invalid generation settings.");
        int count=env->GetArrayLength(roles);if(count<1||count>1024||count!=env->GetArrayLength(contents))throw std::runtime_error("Invalid chat history.");
        std::vector<std::string> names,texts;names.reserve(count);texts.reserve(count);size_t bytes=0;
        for(int i=0;i<count;i++){
            auto role=(jstring)env->GetObjectArrayElement(roles,i),text=(jstring)env->GetObjectArrayElement(contents,i);
            names.push_back(utf8(env,role));texts.push_back(utf8(env,text));env->DeleteLocalRef(role);env->DeleteLocalRef(text);
            if(names.back()!="system"&&names.back()!="user"&&names.back()!="assistant")throw std::runtime_error("Unsupported conversation role.");
            bytes+=texts.back().size();if(bytes>4*1024*1024)throw std::runtime_error("This history exceeds the phone runtime's prompt limit.");
        }
        std::vector<llama_chat_message> messages;for(int i=0;i<count;i++)messages.push_back({names[i].c_str(),texts[i].c_str()});
        const char *tmpl=llama_model_chat_template(model,nullptr);
        if(!tmpl||!tmpl[0])throw std::runtime_error("This model does not contain a supported publisher chat template. Choose an Instruct GGUF or use the paired PC.");
        int needed=llama_chat_apply_template(tmpl,messages.data(),messages.size(),true,nullptr,0);
        if(needed<=0||needed>8*1024*1024)throw std::runtime_error("This publisher chat template is not supported by the phone runtime.");
        std::vector<char> prompt((size_t)needed+1);llama_chat_apply_template(tmpl,messages.data(),messages.size(),true,prompt.data(),(int)prompt.size());
        const auto *vocab=llama_model_get_vocab(model);
        int tokens_needed=llama_tokenize(vocab,prompt.data(),needed,nullptr,0,true,true);
        if(tokens_needed==INT32_MIN)throw std::runtime_error("The prompt is too large to tokenize.");
        tokens_needed=tokens_needed<0?-tokens_needed:tokens_needed;
        if(tokens_needed<1||tokens_needed+32>int(llama_n_ctx(context)))throw std::runtime_error("This chat exceeds the selected phone context window. Start a new chat or increase Phone context in Settings. The app has not dropped your history.");
        std::vector<llama_token> tokens(tokens_needed);int n=llama_tokenize(vocab,prompt.data(),needed,tokens.data(),(int)tokens.size(),true,true);
        if(n<1)throw std::runtime_error("Prompt tokenization failed.");
        llama_memory_clear(llama_get_memory(context),true);
        jclass cls=env->GetObjectClass(callback);jmethodID on_update=env->GetMethodID(cls,"onUpdate","([BIIJ)V");env->DeleteLocalRef(cls);
        if(!on_update)throw std::runtime_error("Phone streaming callback unavailable.");
        auto start=Clock::now();std::string output;update(env,callback,on_update,output,n,0,start);
        for(int at=0;at<n;at+=256){
            if(stopped)return env->NewStringUTF("cancelled");
            auto batch=llama_batch_get_one(tokens.data()+at,std::min(256,n-at));
            int result=llama_decode(context,batch);
            if(result!=0){if(stopped)return env->NewStringUTF("cancelled");throw std::runtime_error("Phone model prefill failed. Reduce the context or choose a smaller model.");}
        }
        sampler=llama_sampler_chain_init(llama_sampler_chain_default_params());
        llama_sampler_chain_add(sampler,llama_sampler_init_penalties(llama_vocab_n_tokens(vocab),64,repeat_penalty,0,0));
        if(temperature==0)llama_sampler_chain_add(sampler,llama_sampler_init_greedy());
        else{llama_sampler_chain_add(sampler,llama_sampler_init_top_k(top_k));llama_sampler_chain_add(sampler,llama_sampler_init_top_p(top_p,1));llama_sampler_chain_add(sampler,llama_sampler_init_temp(temperature));llama_sampler_chain_add(sampler,llama_sampler_init_dist(seed<0?LLAMA_DEFAULT_SEED:(uint32_t)seed));}
        int limit=std::min((int)max_tokens,int(llama_n_ctx(context))-n);std::string reason="length";
        for(int i=0;i<limit;i++){
            if(stopped){reason="cancelled";break;}
            llama_token token=llama_sampler_sample(sampler,context,-1);
            if(llama_vocab_is_eog(vocab,token)){reason="stop";break;}
            char small[256];int length=llama_token_to_piece(vocab,token,small,sizeof(small),0,false);
            if(length<0){std::vector<char> big(-length);length=llama_token_to_piece(vocab,token,big.data(),(int)big.size(),0,false);if(length>0)output.append(big.data(),length);}else if(length>0)output.append(small,length);
            update(env,callback,on_update,output,n,i+1,start);
            if(i+1<limit){auto batch=llama_batch_get_one(&token,1);if(llama_decode(context,batch)!=0){if(stopped){reason="cancelled";break;}throw std::runtime_error("Phone generation failed while decoding. Your partial response is retained.");}}
        }
        llama_sampler_free(sampler);return env->NewStringUTF(reason.c_str());
    }catch(const std::exception &error){if(sampler)llama_sampler_free(sampler);if(!env->ExceptionCheck())fail(env,error.what());return nullptr;}
}
