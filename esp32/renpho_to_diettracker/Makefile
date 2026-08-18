# Host unit tests for ScaleSession (no Arduino / ESP toolchain required).
#
#   make test
#   make clean

CXX      ?= g++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Werror -O0 -g -I.
TEST_BIN  = test/test_scale_session

.PHONY: test clean

test: $(TEST_BIN)
	./$(TEST_BIN)

$(TEST_BIN): test/test_scale_session.cpp scale_session.h
	$(CXX) $(CXXFLAGS) -o $@ test/test_scale_session.cpp

clean:
	rm -f $(TEST_BIN)
