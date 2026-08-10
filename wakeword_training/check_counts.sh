#!/bin/bash
cd /root/wakeword/my_custom_model/seraph
for d in positive_train positive_test negative_train negative_test; do
  echo -n "$d: "
  ls "$d" 2>/dev/null | wc -l
done
