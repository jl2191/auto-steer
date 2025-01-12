# %%
import json
import os

from auto_embeds.metrics import calc_acc_detailed, calc_cos_sim_acc, initialize_loss

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"


import numpy as np
import torch as t
import transformer_lens as tl

from auto_embeds.data import (
    filter_word_pairs,
)
from auto_embeds.utils.misc import repo_path_to_abs_path

np.random.seed(1)
t.manual_seed(1)
t.cuda.manual_seed(1)

# %% model setup
# model = tl.HookedTransformer.from_pretrained_no_processing("bloom-3b")
model = tl.HookedTransformer.from_pretrained_no_processing("bloom-560m")
device = model.cfg.device
d_model = model.cfg.d_model
n_toks = model.cfg.d_vocab_out
datasets_folder = repo_path_to_abs_path("datasets")
model_caches_folder = repo_path_to_abs_path("datasets/model_caches")
token_caches_folder = repo_path_to_abs_path("datasets/token_caches")

# %% filtering
train_file_path = f"{datasets_folder}/muse/zh-en/2_extracted/muse-zh-en-train.json"
test_file_path = f"{datasets_folder}/muse/zh-en/2_extracted/muse-zh-en-test.json"

with open(train_file_path, "r", encoding="utf-8") as file:
    train_word_pairs = json.load(file)

with open(test_file_path, "r", encoding="utf-8") as file:
    test_word_pairs = json.load(file)

print(len(train_word_pairs), len(test_word_pairs))

train_word_pairs = filter_word_pairs(
    model,
    train_word_pairs,
    discard_if_same=True,
    # capture_diff_case=True,
    min_length=2,
    capture_space=False,
    capture_no_space=True,
    print_pairs=True,
    print_number=True,
    verbose_count=True,
    # most_common_english=True,
    # most_common_french=True,
)

test_word_pairs = filter_word_pairs(
    model,
    test_word_pairs,
    discard_if_same=True,
    # capture_diff_case=True,
    min_length=2,
    # capture_space=False,
    capture_no_space=True,
    # print_pairs=True,
    print_number=True,
    # most_common_english=True,
    # most_common_french=True,
)

# %% saving
train_save_path = repo_path_to_abs_path(
    "datasets/muse/zh-en/3_filtered/muse-zh-en-train.json"
)
test_save_path = repo_path_to_abs_path(
    "datasets/muse/zh-en/3_filtered/muse-zh-en-test.json"
)

with open(train_save_path, "w", encoding="utf-8") as f:
    json.dump(train_word_pairs, f, ensure_ascii=False, indent=4)

with open(test_save_path, "w", encoding="utf-8") as f:
    json.dump(test_word_pairs, f, ensure_ascii=False, indent=4)

# %% code for quick training if required (commented out by default)

from auto_embeds.data import tokenize_word_pairs  # noqa: I001
from torch.utils.data import DataLoader, TensorDataset
import torch as t
from auto_embeds.embed_utils import (
    initialize_transform_and_optim,
    train_transform,
)

# translation_file = get_dataset_path(
#     ""
# )

train_en_toks, train_fr_toks, train_en_mask, train_fr_mask = tokenize_word_pairs(
    model, train_word_pairs
)
test_en_toks, test_fr_toks, test_en_mask, test_fr_mask = tokenize_word_pairs(
    model, test_word_pairs
)

train_en_embeds = model.embed.W_E[train_en_toks].detach().clone()
test_en_embeds = model.embed.W_E[test_en_toks].detach().clone()
train_fr_embeds = model.embed.W_E[train_fr_toks].detach().clone()
test_fr_embeds = model.embed.W_E[test_fr_toks].detach().clone()
# all are of shape[batch, pos, d_model]

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

train_dataset = TensorDataset(train_en_embeds, train_fr_embeds)
train_loader = DataLoader(train_dataset, batch_size=128, shuffle=True)

test_dataset = TensorDataset(test_en_embeds, test_fr_embeds)
test_loader = DataLoader(test_dataset, batch_size=128, shuffle=True)

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
        # optim_kwargs={"lr": 1e-4},
        # optim_kwargs={"lr": 6e-5},
        optim_kwargs={"lr": 8e-5, "weight_decay": 2e-5},
    )
    loss_module = initialize_loss("cos_sim")

    if optim is not None:
        transform, loss_history = train_transform(
            model=model,
            train_loader=train_loader,
            test_loader=test_loader,
            transform=transform,
            optim=optim,
            loss_module=loss_module,
            n_epochs=100,
            # neptune=neptune,
        )
    else:
        print(f"nothing trained for {transformation_name}")

    print(f"{transformation_name}:")
    accuracy = calc_acc_detailed(
        model,
        test_loader,
        transform,
        exact_match=False,
        print_results=True,
        print_top_preds=True,
    )
    print(f"{transformation_name}:")
    print(f"Correct Percentage: {accuracy * 100:.2f}%")
    print("Test Accuracy:", calc_cos_sim_acc(test_loader, transform))

    # mark_translation(
    #     model=model,
    #     transformation=transform,
    #     test_loader=test_loader,
    #     azure_translations_path=translation_file,
    #     print_results=True,
    # )
