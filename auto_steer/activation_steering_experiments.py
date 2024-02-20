# %%
import json

import torch as t
import transformer_lens as tl

from auto_steer.steering_utils import (
    calc_cos_sim_acc,
    create_data_loaders,
    initialize_transform_and_optim,
    load_test_strings,
    mean_vec,
    perform_translation_tests,
    run_and_gather_acts,
    save_acts,
    tokenize_texts,
    train_and_evaluate_transform,
)
from auto_steer.utils.misc import (
    repo_path_to_abs_path,
)

# %% model setup
# model = tl.HookedTransformer.from_pretrained_no_processing("bloom-3b")
model = tl.HookedTransformer.from_pretrained_no_processing("bloom-560m")
device = model.cfg.device
d_model = model.cfg.d_model
n_toks = model.cfg.d_vocab_out
datasets_folder = repo_path_to_abs_path("datasets")
cache_folder = repo_path_to_abs_path("datasets/activation_cache")

#%% ------------------------------------------------------------------------------------
# %% kaikki french dictionary
# %% generate activations
with open(f"{datasets_folder}/kaikki-french-dictionary-single-word-pairs.json", "r") as file:
    fr_en_pairs = json.load(file)

#38597 english-french pairs in total
en_strs_list, fr_strs_list = zip(*[(pair["English"], pair["French"]) for pair in fr_en_pairs])
#%%
en_toks, en_attn_mask = tokenize_texts(model, en_strs_list)
fr_toks, fr_attn_mask = tokenize_texts(model, fr_strs_list)
#%%
train_loader, test_loader = create_data_loaders(
    en_toks,
    fr_toks,
    batch_size=16,
    train_ratio=0.99,
    en_attn_mask=en_attn_mask,
    fr_attn_mask=fr_attn_mask,
)
filename_base = "bloom-3b-kaikki"
# %% gather activations
# en_acts, fr_acts = run_and_gather_acts(model, train_loader, layers=[1, 2])
# %% save activations
# save_acts(cache_folder, filename_base, en_acts, fr_acts)
# %% load activations
en_resids = t.load(f"{cache_folder}/bloom-3b-kaikki-en-layers-[1, 2].pt")
fr_resids = t.load(f"{cache_folder}/bloom-3b-kaikki-fr-layers-[1, 2].pt")
#%%
en_resids = {layer: t.cat(en_resids[layer], dim=0) for layer in en_resids}
fr_resids = {layer: t.cat(fr_resids[layer], dim=0) for layer in fr_resids}

layer_idx = 1
gen_length = 20

# %% train en-fr rotation
with open(f"{datasets_folder}/kaikki-french-dictionary-single-word-pairs.json",
          "r") as file:
    fr_en_pairs = json.load(file)

en_file = f"{datasets_folder}/europarl/europarl-v7.fr-en.en"
fr_file = f"{datasets_folder}/europarl/europarl-v7.fr-en.fr"
test_en_strs = load_test_strings(en_file, skip_lines=1000)
test_fr_strs = load_test_strings(fr_file, skip_lines=1000)

en_strs_list, fr_strs_list = zip(*[(pair["English"], pair["French"]) for pair in fr_en_pairs])

generator = t.Generator().manual_seed(42)

def split_dataset(dataset, prop=0.1, print_sizes=False):
    original_size = len(dataset)
    split_size = int(original_size * prop)
    remaining_size = original_size - split_size
    dataset, _ = t.utils.data.random_split(
        dataset, [split_size, remaining_size], generator=generator
    )
    if print_sizes:
        print(f"Original size: {original_size}, Final size: {split_size}")
    return dataset

test_loader, train_loader = create_data_loaders(
     split_dataset(en_resids[layer_idx], prop=0.1, print_sizes=True),
     split_dataset(fr_resids[layer_idx], prop=0.1, print_sizes=True),
     train_ratio=0.99,
     batch_size=128,
     match_dims=True
)
#%%
initial_rotation, optim = initialize_transform_and_optim(
    d_model, transformation="rotation", lr=0.0002, device=device
)

learned_rotation = train_and_evaluate_transform(
    model, train_loader, test_loader, initial_rotation, optim, 1, device
)

#%%
print("Test Accuracy:", calc_cos_sim_acc(test_loader, learned_rotation))

fr_to_en_mean_vec = mean_vec(en_resids[layer_idx], fr_resids[layer_idx])

perform_translation_tests(model, test_en_strs, test_fr_strs, layer_idx, gen_length,
                            learned_rotation)
