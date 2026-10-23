"""Configuration for the GPT-X3 hybrid causal language model."""

from transformers import PretrainedConfig


class GPTX3Config(PretrainedConfig):
    model_type = "gptx3"

    def __init__(
        self, vocab_size=65536, hidden_size=576, num_hidden_layers=24,
        num_attention_heads=9, num_key_value_heads=3, head_dim=64,
        gdn_head_dim=64, rotary_dim=32, gdn_layers_per_gqa=3,
        intermediate_size=1584, max_position_embeddings=8192,
        rope_theta=100000.0, rms_norm_eps=1e-6, gdn_norm_eps=1e-5,
        conv_size=4, use_cache=True, use_fla=True, tie_word_embeddings=True,
        bos_token_id=1, eos_token_id=2, pad_token_id=0, **kwargs,
    ):
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.num_key_value_heads = num_key_value_heads
        self.head_dim = head_dim
        self.gdn_head_dim = gdn_head_dim
        self.rotary_dim = rotary_dim
        self.gdn_layers_per_gqa = gdn_layers_per_gqa
        self.intermediate_size = intermediate_size
        self.max_position_embeddings = max_position_embeddings
        self.rope_theta = rope_theta
        self.rms_norm_eps = rms_norm_eps
        self.gdn_norm_eps = gdn_norm_eps
        self.conv_size = conv_size
        self.use_cache = use_cache
        self.use_fla = use_fla
        if hidden_size % gdn_head_dim or hidden_size != num_attention_heads * head_dim:
            raise ValueError("Head dimensions must match hidden_size")
        if num_attention_heads % num_key_value_heads:
            raise ValueError("Query heads must divide into KV groups")
        if not 0 < rotary_dim <= head_dim or rotary_dim % 2:
            raise ValueError("rotary_dim must be positive, even, and fit head_dim")
        if gdn_layers_per_gqa < 1 or num_hidden_layers % (gdn_layers_per_gqa + 1):
            raise ValueError("Layers must form whole GDN2/GQA groups")
        super().__init__(
            tie_word_embeddings=tie_word_embeddings, bos_token_id=bos_token_id,
            eos_token_id=eos_token_id, pad_token_id=pad_token_id, **kwargs,
        )

    def is_full_attention_layer(self, index):
        return (index + 1) % (self.gdn_layers_per_gqa + 1) == 0
