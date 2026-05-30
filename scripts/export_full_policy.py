#!/usr/bin/env python3
"""Export full inference pipeline (normalizer + encoder + latent_normalizer + actor) as a single ONNX.

Usage:
    python export_full_policy.py --checkpoint /path/to/model_XXXX.pt --output /path/to/policy_full.onnx

The exported ONNX takes term-major history [1, obs_dim] and outputs [1, 12] actions.
Supports both latent-normalized and non-normalized checkpoints.
"""

import argparse
import copy
import os
import sys
import torch
import torch.nn as nn


class LatentNormalizerExport(nn.Module):
    """Simplified latent normalizer for ONNX export (no regions, full 64D at once)."""

    def __init__(self, running_mean: torch.Tensor, running_var: torch.Tensor, epsilon: float = 1e-5):
        super().__init__()
        self.register_buffer("running_mean", running_mean.clone())
        self.register_buffer("running_var", running_var.clone())
        self.epsilon = epsilon

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        std = torch.sqrt(self.running_var + self.epsilon)
        return (latent - self.running_mean) / std


class FullPolicyWrapper(nn.Module):
    """Wraps normalizer + encoder + latent_normalizer + actor into a single forward pass.

    Input:  obs_history [batch, obs_dim]
    Output: actions     [batch, 12]
    """

    def __init__(self, normalizer, encoder, latent_normalizer, actor, num_single_obs: int):
        super().__init__()
        self.normalizer = copy.deepcopy(normalizer)
        self.encoder = copy.deepcopy(encoder)
        self.latent_normalizer = copy.deepcopy(latent_normalizer)
        self.actor = copy.deepcopy(actor)
        self.num_single_obs = num_single_obs

    def forward(self, obs_history: torch.Tensor) -> torch.Tensor:
        normalized = self.normalizer(obs_history)
        latent = self.encoder(normalized)
        if self.latent_normalizer is not None:
            latent = self.latent_normalizer(latent)
        current_obs = normalized[:, -self.num_single_obs:]
        actor_input = torch.cat((current_obs, latent), dim=-1)
        return self.actor(actor_input)


def main():
    parser = argparse.ArgumentParser(description="Export full Walker policy to ONNX")
    parser.add_argument("--checkpoint", type=str, required=True, help="Path to model_XXXX.pt checkpoint")
    parser.add_argument("--output", type=str, default=None, help="Output ONNX path (default: same dir as checkpoint)")
    parser.add_argument("--num-single-obs", type=int, default=47, help="Single frame obs dimension")
    parser.add_argument("--history-length", type=int, default=None, help="History length (auto-detected if omitted)")
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)

    if "model_state_dict" not in checkpoint:
        print("ERROR: checkpoint does not contain 'model_state_dict'")
        sys.exit(1)

    state_dict = checkpoint["model_state_dict"]

    # --- Helper: build MLP from state_dict keys ---
    def build_mlp(prefix: str, activation=nn.ELU):
        keys = sorted([k for k in state_dict if k.startswith(f"{prefix}.") and k.endswith(".weight")])
        layers = []
        for k in keys:
            idx = int(k.split(".")[1])
            out_f, in_f = state_dict[k].shape
            if layers and isinstance(layers[-1], nn.Linear):
                layers.append(activation())
            layers.append(nn.Linear(in_f, out_f))
        mlp = nn.Sequential(*layers)
        mlp_state = {k[len(prefix) + 1:]: v for k, v in state_dict.items() if k.startswith(f"{prefix}.")}
        mlp.load_state_dict(mlp_state)
        return mlp

    encoder = build_mlp("encoder")
    actor = build_mlp("actor")

    print(f"Encoder: {encoder}")
    print(f"Actor:   {actor}")

    # --- Parse obs normalizer ---
    norm_mean_key = "actor_obs_normalizer.running_mean"
    norm_var_key = "actor_obs_normalizer.running_var"

    class EmpiricalNormalization(nn.Module):
        def __init__(self, obs_dim):
            super().__init__()
            self.register_buffer("running_mean", torch.zeros(obs_dim))
            self.register_buffer("running_var", torch.ones(obs_dim))
            self.epsilon = 1e-5

        def forward(self, x):
            return (x - self.running_mean) / torch.sqrt(self.running_var + self.epsilon)

    if norm_mean_key in state_dict:
        obs_dim = state_dict[norm_mean_key].shape[0]
        normalizer = EmpiricalNormalization(obs_dim)
        normalizer.running_mean.copy_(state_dict[norm_mean_key])
        normalizer.running_var.copy_(state_dict[norm_var_key])
        print(f"Obs normalizer: EmpiricalNormalization(obs_dim={obs_dim})")
    else:
        normalizer = nn.Identity()
        print("Obs normalizer: Identity (none found)")

    # --- Parse latent normalizer ---
    latent_mean_key = "latent_normalizer.running_mean"
    latent_var_key = "latent_normalizer.running_var"

    if latent_mean_key in state_dict:
        latent_norm = LatentNormalizerExport(
            running_mean=state_dict[latent_mean_key],
            running_var=state_dict[latent_var_key],
        )
        print(f"Latent normalizer: LatentNormalizerExport(dim={state_dict[latent_mean_key].shape[0]})")
        print(f"  running_mean range: [{state_dict[latent_mean_key].min():.4f}, {state_dict[latent_mean_key].max():.4f}]")
        print(f"  running_var  range: [{state_dict[latent_var_key].min():.4f}, {state_dict[latent_var_key].max():.4f}]")
    else:
        latent_norm = None
        print("Latent normalizer: None (not found in checkpoint)")

    # --- Auto-detect history length from encoder input dim ---
    encoder_input_dim = list(encoder.parameters())[0].shape[1]
    if args.history_length is not None:
        history_length = args.history_length
    else:
        history_length = encoder_input_dim // args.num_single_obs
    input_dim = args.num_single_obs * history_length

    if encoder_input_dim != input_dim:
        print(f"WARNING: encoder input dim ({encoder_input_dim}) != num_single_obs * history_length ({input_dim})")
        input_dim = encoder_input_dim

    print(f"Input dim: {input_dim} ({args.num_single_obs}D × {history_length} frames)")

    # --- Build full pipeline ---
    full_model = FullPolicyWrapper(normalizer, encoder, latent_norm, actor, args.num_single_obs)
    full_model.eval()

    dummy_input = torch.zeros(1, input_dim)

    with torch.no_grad():
        dummy_output = full_model(dummy_input)

    print(f"Output: actions [{1}, {dummy_output.shape[1]}]")

    # --- Export to ONNX ---
    if args.output is None:
        output_dir = os.path.join(os.path.dirname(args.checkpoint), "exported")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "policy.onnx")
    else:
        output_path = args.output
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    torch.onnx.export(
        full_model,
        dummy_input,
        output_path,
        opset_version=18,
        input_names=["obs"],
        output_names=["actions"],
        dynamic_axes={},
    )

    # --- Verify ONNX ---
    import onnxruntime as ort
    sess = ort.InferenceSession(output_path)
    for inp in sess.get_inputs():
        print(f"  ONNX Input:  {inp.name} shape={inp.shape} type={inp.type}")
    for out in sess.get_outputs():
        print(f"  ONNX Output: {out.name} shape={out.shape} type={out.type}")

    import numpy as np
    onnx_out = sess.run(None, {"obs": dummy_input.numpy()})[0]
    torch_out = dummy_output.numpy()
    max_diff = np.max(np.abs(onnx_out - torch_out))
    print(f"  Max diff (torch vs onnx): {max_diff:.8f}")

    print(f"\nExported to: {output_path}")
    print(f"Copy to policy dir:  cp {output_path} ~/ros2_ws/src/p73_cc/policy/policy.onnx")


if __name__ == "__main__":
    main()
