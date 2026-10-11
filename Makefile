# HERE task runner (README §22). On Windows without make: .\tasks.ps1 <task>
PY ?= .venv/Scripts/python
ifeq ($(wildcard $(PY)),)
PY = python3
endif

.PHONY: relay laptop-mock laptop-real fake-events tap test schemas mock-scene check golden \
        demo-up demo-reset demo-down demo-status demo-check latency

relay:           ## local relay on :8080 (also the local fallback relay)
	$(PY) -m relay --host 0.0.0.0 --port 8080

laptop-mock:     ## synthetic scene through the real pipeline, no hardware
	$(PY) -m laptop --mock

laptop-real:     ## ReSpeaker XVF3800 -> relay
	$(PY) -m laptop --real

fake-events:     ## scripted core moments for the phone (no audio, no models)
	$(PY) tools/fake_events.py --loop

tap:             ## print every message in the session
	$(PY) tools/relay_tap.py --validate

test:            ## contracts, relay, laptop, smoke + consistency
	$(PY) -m pytest contracts relay laptop tests extras -q

schemas:         ## regenerate contracts/schemas after editing contracts/build_schemas.py
	$(PY) contracts/build_schemas.py

mock-scene:      ## rebuild fixtures/mock_scene (Windows text-to-speech)
	$(PY) tools/make_mock_scene.py

check:           ## pre-demo: GPU, models, ReSpeaker, relay
	$(PY) tools/check_gpu.py

golden:          ## pass/fail regression gate: mock + real-array scenes, babble only (~4 min, GPU)
	$(PY) tools/stress_test.py --golden

demo-up:         ## demo stack: local relay + source + dashboard + memory API (ARGS="--source real")
	$(PY) tools/demo.py up $(ARGS)

demo-reset:      ## between judge groups: relay + dashboard restart, laptop stays warm (ARGS=--full for all)
	$(PY) tools/demo.py reset $(ARGS)

demo-down:
	$(PY) tools/demo.py down

demo-status:
	$(PY) tools/demo.py status

demo-check:      ## pre-demo check of the running stack (no model loads, array left alone)
	$(PY) tools/check_gpu.py --demo

latency:         ## relay latency from here (local or Vultr): p50/p95/max, lost frames (ARGS="--relay wss://host/ws")
	$(PY) tools/latency_probe.py $(ARGS)
