# %%
import json
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

import torch as t
from torch.utils.data import TensorDataset, DataLoader
import random
import numpy as np
import transformer_lens as tl
from IPython.core.getipython import get_ipython
import wandb

from auto_steer.steering_utils import (
    calc_cos_sim_acc,
    evaluate_accuracy,
    initialize_transform_and_optim,
    train_transform,
    run_and_gather_acts,
    filter_word_pairs,
    tokenize_word_pairs,
    save_acts,
    load_test_strings,
    initialize_loss,
    perform_steering_tests,
)
from auto_steer.utils.misc import repo_path_to_abs_path

ipython = get_ipython()
np.random.seed(1)
t.manual_seed(1)
t.cuda.manual_seed(1)
try:
    get_ipython().run_line_magic("load_ext", "autoreload")  # type: ignore
    get_ipython().run_line_magic("load_ext", "line_profiler")  # type: ignore
    get_ipython().run_line_magic("autoreload", "2")  # type: ignore
except:
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
random.seed(1)
random.shuffle(word_pairs)
split_index = int(len(word_pairs) * 0.95)
train_en_fr_pairs = word_pairs[:split_index]
test_en_fr_pairs = word_pairs[split_index:]

train_word_pairs = filter_word_pairs(
    model,
    train_en_fr_pairs,
    discard_if_same=True,
    min_length=6,
    # capture_diff_case=True,
    capture_space=True,
    capture_no_space=True,
    print_pairs=True,
    print_number=True,
    max_token_id=100_000,
    # most_common_english=True,
    # most_common_french=True,
)

test_word_pairs = filter_word_pairs(
    model,
    test_en_fr_pairs,
    discard_if_same=True,
    min_length=6,
    # capture_diff_case=True,
    capture_space=True,
    capture_no_space=True,
    # print_pairs=True,
    print_number=True,
    max_token_id=100_000,
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
t.save(
    {
        "en_toks": train_en_toks,
        "fr_toks": train_fr_toks,
        "en_mask": train_en_mask,
        "fr_mask": train_fr_mask,
    },
    f"{token_caches_folder}/wikdict-train-en-fr-tokens.pt",
)

t.save(
    {
        "en_toks": test_en_toks,
        "fr_toks": test_fr_toks,
        "en_mask": test_en_mask,
        "fr_mask": test_fr_mask,
    },
    f"{token_caches_folder}/wikdict-test-en-fr-tokens.pt",
)
# %%
train_data = t.load(f"{token_caches_folder}/wikdict-train-en-fr-tokens.pt")
test_data = t.load(f"{token_caches_folder}/wikdict-test-en-fr-tokens.pt")

train_en_toks = train_data["en_toks"]
train_fr_toks = train_data["fr_toks"]
train_en_mask = train_data["en_mask"]
train_fr_mask = train_data["fr_mask"]

test_en_toks = test_data["en_toks"]
test_fr_toks = test_data["fr_toks"]
test_en_mask = test_data["en_mask"]
test_fr_mask = test_data["fr_mask"]

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
        # optim_kwargs={"lr": 2e-4, "weight_decay": 1e-4},
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
        print_results=True,
        # print_top_preds=False,
    )
    print(f"{transformation_name}:")
    print(f"Correct Percentage: {accuracy * 100:.2f}%")
    print("Test Accuracy:", calc_cos_sim_acc(test_loader, transform))

# %%
