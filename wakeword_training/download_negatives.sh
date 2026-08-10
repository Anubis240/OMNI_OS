#!/bin/bash
set -e
cd /root/wakeword
wget -q --show-progress -O openwakeword_features_ACAV100M_2000_hrs_16bit.npy \
  "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
wget -q --show-progress -O validation_set_features.npy \
  "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy"
echo "NEGATIVES_DOWNLOAD_DONE"
