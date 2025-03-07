# Copyright © 2023-2024 Apple Inc.

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import mlx.core as mx
import mlx.nn as nn
from mlx_lm.models.su_rope import SuScaledRotaryEmbedding
from transformers import AutoProcessor

from ..base import BaseModelConfig, create_attention_mask
from .multimodal import Phi4MMImageAudioEmbedding
from .processing_phi4mm import InputMode, Phi4MMProcessor
from .vision import VisionConfig

AutoProcessor.register("phi4mm", Phi4MMProcessor)


@dataclass
class ModelConfig(BaseModelConfig):
    model_type: str
    hidden_size: int
    num_hidden_layers: int
    intermediate_size: int
    num_attention_heads: int
    rms_norm_eps: float
    vocab_size: int
    num_key_value_heads: Optional[int] = None
    rope_theta: float = 10000
    rope_traditional: bool = False
    rope_scaling: Optional[Dict[str, Union[float, List[float]]]] = None
    partial_rotary_factor: float = 1.0
    max_position_embeddings: int = 131072
    original_max_position_embeddings: int = 4096
    tie_word_embeddings: bool = False
    embd_layer: Optional[Dict[str, str]] = None
    image_size: Optional[int] = 224
    patch_size: Optional[int] = 14
    audio_processor: Optional[Dict[str, Any]] = None
    vision_lora: Optional[Dict[str, Any]] = None
    speech_lora: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.num_key_value_heads is None:
            self.num_key_value_heads = self.num_attention_heads

        if self.rope_scaling:
            required_keys = {"long_factor", "type"}
            if not all(key in self.rope_scaling for key in required_keys):
                raise ValueError(f"rope_scaling must contain keys {required_keys}")

            if self.rope_scaling["type"] not in ["longrope", "su", "linear"]:
                print(
                    "[WARNING] rope_scaling 'type' currently only supports 'linear', 'su', and 'longrope'; setting rope scaling to false."
                )
                self.rope_scaling = None


class Attention(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()

        dim = config.hidden_size
        self.n_heads = n_heads = config.num_attention_heads
        assert config.num_key_value_heads is not None
        self.n_kv_heads = n_kv_heads = config.num_key_value_heads
        self.num_hidden_layers = config.num_hidden_layers

        self.head_dim = head_dim = config.hidden_size // n_heads
        self.scale = head_dim**-0.5

        op_size = n_heads * head_dim + 2 * (n_kv_heads * head_dim)
        self.qkv_proj = nn.Linear(dim, op_size, bias=False)
        self.o_proj = nn.Linear(n_heads * head_dim, dim, bias=False)

        rope_dim = int(head_dim * config.partial_rotary_factor)
        if config.rope_scaling and config.rope_scaling["type"] in ["longrope", "su"]:
            self.rope = SuScaledRotaryEmbedding(
                rope_dim,
                base=config.rope_theta,
                max_position_embeddings=config.max_position_embeddings,
                original_max_position_embeddings=config.original_max_position_embeddings,
                short_factor=config.rope_scaling["short_factor"],
                long_factor=config.rope_scaling["long_factor"],
            )
        else:
            rope_scale = 1.0
            if config.rope_scaling and config.rope_scaling["type"] == "linear":
                assert isinstance(config.rope_scaling["factor"], float)
                rope_scale = 1 / config.rope_scaling["factor"]
            self.rope = nn.RoPE(
                rope_dim,
                traditional=config.rope_traditional,
                base=config.rope_theta,
                scale=rope_scale,
            )

    def __call__(
        self,
        x: mx.array,
        mask: Optional[mx.array] = None,
        cache: Optional[Any] = None,
    ) -> mx.array:
        B, L, D = x.shape

        qkv = self.qkv_proj(x)
        query_pos = self.n_heads * self.head_dim
        queries, keys, values = mx.split(
            qkv, [query_pos, query_pos + self.n_kv_heads * self.head_dim], axis=-1
        )

        # Prepare the queries, keys and values for the attention computation
        queries = queries.reshape(B, L, self.n_heads, -1).transpose(0, 2, 1, 3)
        keys = keys.reshape(B, L, self.n_kv_heads, -1).transpose(0, 2, 1, 3)
        values = values.reshape(B, L, self.n_kv_heads, -1).transpose(0, 2, 1, 3)

        if cache is not None:
            queries = self.rope(queries, offset=cache.offset)
            keys = self.rope(keys, offset=cache.offset)
            keys, values = cache.update_and_fetch(keys, values)
        else:
            queries = self.rope(queries)
            keys = self.rope(keys)

        output = mx.fast.scaled_dot_product_attention(
            queries, keys, values, cache=cache, scale=self.scale, mask=mask
        )
        output = output.transpose(0, 2, 1, 3).reshape(B, L, -1)
        return self.o_proj(output)


class MLP(nn.Module):
    def __init__(self, dim, hidden_dim):
        super().__init__()
        self.gate_up_proj = nn.Linear(dim, 2 * hidden_dim, bias=False)
        self.down_proj = nn.Linear(hidden_dim, dim, bias=False)

    def __call__(self, x) -> mx.array:
        x = self.gate_up_proj(x)
        gate, x = mx.split(x, 2, axis=-1)
        return self.down_proj(nn.silu(gate) * x)


class TransformerBlock(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.num_attention_heads = config.num_attention_heads
        self.hidden_size = config.hidden_size
        self.self_attn = Attention(config)
        self.mlp = MLP(config.hidden_size, config.intermediate_size)
        self.input_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.post_attention_layernorm = nn.RMSNorm(
            config.hidden_size, eps=config.rms_norm_eps
        )

    def __call__(
        self,
        x: mx.array,
        mask: Optional[mx.array] = None,
        cache: Optional[Any] = None,
    ) -> mx.array:
        r = self.self_attn(self.input_layernorm(x), mask, cache)
        h = x + r
        r = self.mlp(self.post_attention_layernorm(h))
        out = h + r
        return out


class Phi4Model(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.config = config
        self.vocab_size = config.vocab_size
        self.num_hidden_layers = config.num_hidden_layers
        assert self.vocab_size > 0
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.embed_tokens_extend = None
        if isinstance(config.embd_layer, dict):
            embedding_config = {
                "embedding_cls": config.embd_layer["embedding_cls"],
                **config.embd_layer,
            }
            self.embed_tokens_extend = Phi4MMImageAudioEmbedding(
                config, **embedding_config
            )
        self.layers = [
            TransformerBlock(config=config) for _ in range(config.num_hidden_layers)
        ]
        self.norm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)

        # LoRA related settings
        assert getattr(config, "vision_lora", None) is not None
        import re

        from ...trainer.utils import LoRaLayer, set_module_by_name

        for name, module in self.named_modules():

            if isinstance(module, nn.Linear) or isinstance(module, nn.QuantizedLinear):
                if re.match(config.vision_lora["layer"], name):
                    # print(f"Applying Vision LoRA to {name}")
                    lora_layer = LoRaLayer(
                        module,
                        config.vision_lora["r"],
                        config.vision_lora["lora_alpha"],
                        config.vision_lora["dp"],
                        "vision",
                    )
                    set_module_by_name(self, name, lora_layer)

        self.config.vision_lora["r"] = config.vision_lora["r"]
        self.config.vision_lora["lora_alpha"] = config.vision_lora["lora_alpha"]
        self.config.vision_lora["layer"] = config.vision_lora["layer"]
        self.config.vision_lora["dp"] = config.vision_lora["dp"]

        assert getattr(config, "speech_lora", None) is not None
        for name, module in self.named_modules():
            if isinstance(module, nn.Linear) or isinstance(module, nn.QuantizedLinear):
                if re.match(config.speech_lora["layer"], name):
                    # print(f"Applying Speech LoRA to {name}")
                    lora_layer = LoRaLayer(
                        module,
                        config.speech_lora["r"],
                        config.speech_lora["lora_alpha"],
                        config.speech_lora["dp"],
                        "speech",
                    )
                    name = name.replace(".base_layer", "")
                    set_module_by_name(self, name, lora_layer)

        self.config.speech_lora["r"] = config.speech_lora["r"]
        self.config.speech_lora["lora_alpha"] = config.speech_lora["lora_alpha"]
        self.config.speech_lora["layer"] = config.speech_lora["layer"]
        self.config.speech_lora["dp"] = config.speech_lora["dp"]

    def __call__(
        self,
        input_ids: mx.array,
        pixel_values: mx.array,
        mask: mx.array,
        cache=None,
        **kwargs,
    ):
        input_mode = kwargs.pop("input_mode", None)
        if isinstance(input_mode, mx.array):
            assert len(input_mode) == 1
            input_mode = input_mode[0].item()
        input_mode = InputMode(input_mode)

        if input_mode in [InputMode.VISION_SPEECH, InputMode.VISION]:
            self.set_lora_adapter("vision")
            audio_projection_mode = "vision"
        elif input_mode == InputMode.SPEECH:
            self.set_lora_adapter("speech")
            audio_projection_mode = "speech"
        elif input_mode == InputMode.LANGUAGE:
            self.unset_lora_adapter()
            audio_projection_mode = "speech"
        else:
            raise ValueError(f"Invalid input_mode: {input_mode}")

        if pixel_values is None:
            h = self.embed_tokens_extend(
                input_ids=input_ids,
                input_embeds=None,
                input_image_embeds=kwargs.pop("input_image_embeds", None),
                input_audio_embeds=kwargs.pop("input_audio_embeds", None),
                image_sizes=kwargs.pop("image_sizes", None),
                image_attention_mask=kwargs.pop("image_attention_mask", None),
                audio_embed_sizes=kwargs.pop("audio_embed_sizes", None),
                audio_attention_mask=kwargs.pop("audio_attention_mask", None),
                audio_projection_mode=audio_projection_mode,
                wte=self.embed_tokens,
            )
        else:
            h = self.embed_tokens(input_ids)

        if mask is None:
            mask = create_attention_mask(h, cache)

        if cache is None:
            cache = [None] * len(self.layers)

        for layer, c in zip(self.layers, cache):
            h = layer(h, mask, c)

        return self.norm(h)


class Model(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        self.model_type = config.model_type
        self.model = Phi4Model(config)
        if not config.tie_word_embeddings:
            self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        self.config = config

    def __call__(
        self,
        input_ids: mx.array,
        pixel_values: mx.array,
        mask: mx.array,
        cache=None,
        **kwargs,
    ):
        out = self.model(input_ids, pixel_values, mask, cache, **kwargs)
        if self.config.tie_word_embeddings:
            out = self.model.embed_tokens.as_linear(out)
        else:
            out = self.lm_head(out)
        return out

    @property
    def layers(self):
        return self.model.layers

    def sanitize(self, weights):
        weights = self.model.embed_tokens_extend.image_embed.sanitize(weights)
        weights = self.model.embed_tokens_extend.audio_embed.sanitize(weights)
        return weights
