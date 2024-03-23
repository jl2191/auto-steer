# %%
import json
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

import random

import numpy as np
import torch as t
import transformer_lens as tl
from IPython.core.getipython import get_ipython
from torch.utils.data import DataLoader, TensorDataset

from auto_embeds.embed_utils import (
    calc_cos_sim_acc,
    evaluate_accuracy,
    filter_word_pairs,
    initialize_loss,
    initialize_transform_and_optim,
    tokenize_word_pairs,
    train_transform,
    mark_correct,
)
from auto_embeds.utils.misc import repo_path_to_abs_path

ipython = get_ipython()
np.random.seed(1)
t.manual_seed(1)
t.cuda.manual_seed(1)
try:
    get_ipython().run_line_magic("load_ext", "autoreload")  # type: ignore
    get_ipython().run_line_magic("load_ext", "line_profiler")  # type: ignore
    get_ipython().run_line_magic("autoreload", "2")  # type: ignore
except Exception:
    pass

# %% model setup
# model = tl.HookedTransformer.from_pretrained_no_processing("mistral-7b")
model = tl.HookedTransformer.from_pretrained_no_processing("bloom-560m")
device = model.cfg.device
d_model = model.cfg.d_model
n_toks = model.cfg.d_vocab_out
datasets_folder = repo_path_to_abs_path("datasets")
model_caches_folder = repo_path_to_abs_path("datasets/model_caches")
token_caches_folder = repo_path_to_abs_path("datasets/token_caches")

# %% -----------------------------------------------------------------------------------
file_path = f"{datasets_folder}/wikdict/2_extracted/eng-fra.json"
# file_path = f"{datasets_folder}/cc-cedict/cedict-zh-en.json"
with open(file_path, "r") as file:
    word_pairs = json.load(file)
# random.seed(1)
# random.shuffle(word_pairs)
split_index = int(len(word_pairs) * 0.8)

test_split_start = int(len(word_pairs) * 0.4)
test_split_end = int(len(word_pairs) * 0.5)

test_en_fr_pairs = word_pairs[test_split_start:test_split_end]
train_en_fr_pairs = [word_pair for word_pair in word_pairs if word_pair not in test_en_fr_pairs]

train_word_pairs = filter_word_pairs(
    model,
    train_en_fr_pairs,
    discard_if_same=True,
    min_length=4,
    # capture_diff_case=True,
    capture_space=True,
    # capture_no_space=True,
    # print_pairs=True,
    print_number=True,
    # max_token_id=100_000,
    # most_common_english=True,
    # most_common_french=True,
)

test_word_pairs = filter_word_pairs(
    model,
    test_en_fr_pairs,
    discard_if_same=True,
    min_length=4,
    # capture_diff_case=True,
    capture_space=True,
    # capture_no_space=True,
    # print_pairs=True,
    print_number=True,
    # max_token_id=100_000,
    # most_common_english=True,
    # most_common_french=True,
)

train_en_toks, train_fr_toks, train_en_mask, train_fr_mask = tokenize_word_pairs(
    model, train_word_pairs
)
test_en_toks, test_fr_toks, test_en_mask, test_fr_mask = tokenize_word_pairs(
    model, test_word_pairs
)

# %%
train_en_embeds = (
    model.embed.W_E[train_en_toks].detach().clone()
)  # shape[batch, pos, d_model]
test_en_embeds = (
    model.embed.W_E[test_en_toks].detach().clone()
)  # shape[batch, pos, d_model]
train_fr_embeds = (
    model.embed.W_E[train_fr_toks].detach().clone()
)  # shape[batch, pos, d_model]
test_fr_embeds = (
    model.embed.W_E[test_fr_toks].detach().clone()
)  # shape[batch, pos, d_model]

# train_en_embeds = t.nn.functional.layer_norm(
#     model.embed.W_E[train_en_toks].detach().clone(), [model.cfg.d_model]
# )
# train_fr_embeds = t.nn.functional.layer_norm(
#     model.embed.W_E[train_fr_toks].detach().clone(), [model.cfg.d_model]
# )
# test_en_embeds = t.nn.functional.layer_norm(
#     model.embed.W_E[test_en_toks].detach().clone(), [model.cfg.d_model]
# )
# test_fr_embeds = t.nn.functional.layer_norm(
#     model.embed.W_E[test_fr_toks].detach().clone(), [model.cfg.d_model]
# )

print(train_en_embeds.shape)
train_dataset = TensorDataset(train_en_embeds, train_fr_embeds)
train_loader = DataLoader(train_dataset, batch_size=128, shuffle=True)

test_dataset = TensorDataset(test_en_embeds, test_fr_embeds)
test_loader = DataLoader(test_dataset, batch_size=128, shuffle=True)
# %%
# run = wandb.init(
#     project="single_token_tests",
# )
# %%

translation_file = repo_path_to_abs_path(
    "datasets/azure_translator/bloom-en-fr-all-translations.json"
)

transformation_names = [
    # "identity",
    # "translation",
    "linear_map",
    # "biased_linear_map",
    # "uncentered_linear_map",
    # "biased_uncentered_linear_map",
    # "rotation",
    # "biased_rotation",
    # "uncentered_rotation",
]

for transformation_name in transformation_names:

    transform = None
    optim = None

    transform, optim = initialize_transform_and_optim(
        d_model,
        transformation=transformation_name,
        optim_kwargs={"lr": 2e-4},
    )
    loss_module = initialize_loss("cosine_similarity")

    if optim is not None:
        transform, loss_history = train_transform(
            model=model,
            train_loader=train_loader,
            test_loader=test_loader,
            transform=transform,
            optim=optim,
            loss_module=loss_module,
            n_epochs=100,
            # wandb=wandb,
        )
    else:
        print(f"nothing trained for {transformation_name}")

    print(f"{transformation_name}:")
    accuracy = evaluate_accuracy(
        model,
        test_loader,
        transform,
        exact_match=False,
        # print_results=True,
        # print_top_preds=False,
    )
    print(f"{transformation_name}:")
    print(f"Correct Percentage: {accuracy * 100:.2f}%")
    print("Test Accuracy:", calc_cos_sim_acc(test_loader, transform))


# %%
mark_correct(
    model=model,
    transformation=transform,
    test_loader=test_loader,
    acceptable_translations_path=translation_file,
    print_results=True
)

# %%
import einops
from auto_embeds.embed_utils import get_most_similar_embeddings
with t.no_grad():
    for batch in test_loader:
        en_embeds, fr_embeds = batch
        en_logits = einops.einsum(
            en_embeds,
            model.embed.W_E,
            "batch pos d_model, d_vocab d_model -> batch pos d_vocab",
        )
        en_strs = model.to_str_tokens(en_logits.argmax(dim=-1))  # type: ignore
        fr_logits = einops.einsum(
            fr_embeds,
            model.embed.W_E,
            "batch pos d_model, d_vocab d_model -> batch pos d_vocab",
        )
        fr_strs = model.to_str_tokens(fr_logits.argmax(dim=-1))  # type: ignore
        with t.no_grad():
            pred = transform(en_embeds)
        pred_logits = einops.einsum(
            pred,
            model.embed.W_E,
            "batch pos d_model, d_vocab d_model -> batch pos d_vocab",
        )
        pred_top_strs = model.to_str_tokens(pred_logits.argmax(dim=-1))
        pred_top_strs = [
            item if isinstance(item, str) else item[0] for item in pred_top_strs
        ]
        assert all(isinstance(item, str) for item in pred_top_strs)
        most_similar_embeds = get_most_similar_embeddings(
            model,
            out=pred,
            top_k=4,
            apply_embed=True,
        )
        total = 0
        same = 0
        for i, pred_top_str in enumerate(pred_top_strs):
            en_str = en_strs[i]
            fr_str = fr_strs[i]
            if pred_top_str == en_str:
                same += 1
            total += 1

print(same/total)