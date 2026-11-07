PYTHON ?= python3
CC = clang

.PHONY: all test clean
all: build/libkp20d-native-hid.dylib

build/libkp20d-native-hid.dylib: tools/kp20d-native-hid.m
	mkdir -p build
	$(CC) -O2 -mmacosx-version-min=12.0 -dynamiclib -framework Foundation -framework IOKit $< -o $@

test:
	bash -n 刷写BIN固件.command
	bash -n 刷入Xbox补丁.command
	bash -n 恢复官方固件.command
	$(PYTHON) -m unittest discover -s tests -p 'test_builder.py'
	$(PYTHON) -m unittest discover -s tests -p 'test_one_click.py'
	$(PYTHON) -m unittest discover -s tests -p 'test_custom_firmware.py'
	$(PYTHON) tests/test_transactions.py
	$(PYTHON) tools/native_updater.py --help

clean:
	rm -f build/libkp20d-native-hid.dylib build/offline-transactions.json
