PYTHON ?= python3
CC = clang

.PHONY: all test clean
all: build/libkp20d-native-hid.dylib

build/libkp20d-native-hid.dylib: tools/kp20d-native-hid.m
	mkdir -p build
	$(CC) -O2 -mmacosx-version-min=12.0 -dynamiclib -framework Foundation -framework IOKit $< -o $@

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_builder.py'
	$(PYTHON) tests/test_transactions.py
	$(PYTHON) tools/native_updater.py --help

clean:
	rm -f build/libkp20d-native-hid.dylib build/offline-transactions.json
