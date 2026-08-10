#!/bin/bash
for url in \
  "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy" \
  "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy" \
  "https://huggingface.co/datasets/agkphysics/AudioSet/resolve/main/data/bal_train09.tar" ; do
  echo "$url"
  curl -sIL "$url" | grep -i content-length | tail -1
  echo "---"
done
