import numpy as np
from openwakeword.model import Model

m = Model(wakeword_models=["/root/wakeword/my_custom_model/seraph.onnx"], inference_framework="onnx")
print("Loaded OK. Models:", list(m.models.keys()))

# Feed a few seconds of silence/noise through it to make sure inference runs cleanly
rng = np.random.default_rng(0)
for i in range(20):
    chunk = (rng.standard_normal(1280) * 50).astype(np.int16)  # ~quiet noise, 80ms @16kHz
    scores = m.predict(chunk)
    print(i, scores)
