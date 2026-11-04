// Exercise the exact production renderer without loading weights or starting JNI.
#include "../native/phone_runtime.cpp"
#include <fstream>
#include <iostream>
#include <iterator>

static void check(bool value,const char *message){if(!value)throw std::runtime_error(message);}
static std::string read(const char *path){std::ifstream input(path,std::ios::binary);if(!input)throw std::runtime_error("Missing pinned fixture");return std::string(std::istreambuf_iterator<char>(input),{});}

int main(int argc,char **argv){
    try{
        check(argc==3,"Expected metadata and external template fixture paths");
        auto metadata=common_json::parse(read(argv[1]));
        auto standard=metadata.at("gguf").at("chat_template").get<std::string>();
        std::string bos="<|startoftext|>",eos="<|im_end|>";
        auto rendered=render_chat(standard,{"user"},{"Hi"},bos,eos);
        check(rendered==bos+"<|im_start|>user\nHi"+eos+"\n<|im_start|>assistant\n<think>","LFM3 exact single-turn Jinja formatting");
        auto external=read(argv[2]);
        auto latent=render_chat(external,{"system","user"},{"Be brief.","Hi"},bos,eos);
        check(latent.find("<|im_start|>system\nBe brief.")!=std::string::npos,"Preserve system message");
        check(latent.find("<|im_start|>assistant\n<think><|latent_0|>")!=std::string::npos,"Use publisher external latent prefix");
        check(latent.find("<|latent_15|>\n</think>\n\n")!=std::string::npos,"Preserve exact final latent prefix");
        auto multi=render_chat(standard,{"user","assistant","user"},{"First","<think>private prior thought</think>First answer","Second"},bos,eos);
        check(multi.find("private prior thought")==std::string::npos,"Publisher template removes prior thought");
        check(multi.find("First answer")!=std::string::npos&&multi.find("Second")!=std::string::npos,"Keep actual multi-turn text");
        auto unicode=render_chat("{{ bos_token }}{% for m in messages %}{{ m['role'] }}:{{ m['content'] }}|{% endfor %}{% if add_generation_prompt %}assistant:{% endif %}",{"user"},{"שלום 世界"},"<s>","</s>");
        check(unicode=="<s>user:שלום 世界|assistant:","UTF-8 and ordinary templates");
        auto macro=render_chat("{% macro item(x) %}{{ x|upper }}{% endmacro %}{% set ns=namespace(n=0) %}{% for m in messages %}{% set ns.n=ns.n+1 %}{{ item(m.content) }}{% endfor %}:{{ ns.n }}",{"user"},{"hello"},"","");
        check(macro=="HELLO:1","Macros filters and namespace execute through full Jinja");
        bool rejected=false;try{render_chat("{% if broken %}",{"user"},{"secret"},"","");}catch(const std::exception &){rejected=true;}
        check(rejected,"Malformed templates must fail explicitly");
        std::cout<<"Native full Jinja, exact LFM3, pinned latent template, multi-turn and UTF-8 checks passed\n";
        return 0;
    }catch(const std::exception &error){std::cerr<<error.what()<<"\n";return 1;}
}
