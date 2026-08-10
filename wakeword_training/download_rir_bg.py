import os
import numpy as np
import scipy.io.wavfile
import datasets
from pathlib import Path
from tqdm import tqdm

os.chdir("/root/wakeword")

# MIT room impulse responses
output_dir = "./mit_rirs"
os.makedirs(output_dir, exist_ok=True)
if len(os.listdir(output_dir)) == 0:
    print("Downloading MIT RIRs...")
    rir_dataset = datasets.load_dataset(
        "davidscripka/MIT_environmental_impulse_responses", split="train", streaming=True
    )
    for row in tqdm(rir_dataset):
        name = row["audio"]["path"].split("/")[-1]
        scipy.io.wavfile.write(
            os.path.join(output_dir, name), 16000, (row["audio"]["array"] * 32767).astype(np.int16)
        )
else:
    print("MIT RIRs already present, skipping")

# FMA background music (1 hour, matching the example notebook's scope)
output_dir = "./fma"
os.makedirs(output_dir, exist_ok=True)
if len(os.listdir(output_dir)) == 0:
    print("Downloading FMA background clips...")
    fma_dataset = datasets.load_dataset("rudraml/fma", name="small", split="train", streaming=True)
    fma_dataset = iter(fma_dataset.cast_column("audio", datasets.Audio(sampling_rate=16000)))
    n_hours = 1
    n_clips = n_hours * 3600 // 30
    for i in tqdm(range(n_clips)):
        row = next(fma_dataset)
        name = row["audio"]["path"].split("/")[-1].replace(".mp3", ".wav")
        scipy.io.wavfile.write(
            os.path.join(output_dir, name), 16000, (row["audio"]["array"] * 32767).astype(np.int16)
        )
else:
    print("FMA clips already present, skipping")

print("RIR_BG_DONE")
