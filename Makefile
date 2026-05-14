CC      = gcc
CFLAGS  = -std=c99 -Wall -Wextra -g -O2 -Iinclude -Ilib -fPIC
LDFLAGS = -lpthread -lm

LIB_DIR  = lib
INC_DIR  = include
EX_C_DIR = examples/c
TEST_DIR = test/c
OUT_DIR  = /usr/local/lib
HDR_DIR  = /usr/local/include

LIBRARY  = libprismc.so

LIB_SRCS = $(LIB_DIR)/transport.c \
           $(LIB_DIR)/protocol.c  \
           $(LIB_DIR)/scan.c      \
           $(LIB_DIR)/prismc.c

LIB_OBJS = $(LIB_SRCS:.c=.o)

EX_SRCS  = $(wildcard $(EX_C_DIR)/*.c)
EX_BINS  = $(patsubst $(EX_C_DIR)/%.c, $(EX_C_DIR)/%, $(EX_SRCS))

TEST_SRCS = $(wildcard $(TEST_DIR)/*.c)
TEST_BINS = $(patsubst $(TEST_DIR)/%.c, $(TEST_DIR)/%, $(TEST_SRCS))

.PHONY: all lib examples test install uninstall clean

all: lib

# ── Shared library ────────────────────────────────────────────────────────────
lib: $(LIBRARY)

$(LIBRARY): $(LIB_OBJS)
	$(CC) -shared -o $@ $^ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

# ── Examples ──────────────────────────────────────────────────────────────────
examples: lib $(EX_BINS)

$(EX_C_DIR)/%: $(EX_C_DIR)/%.c
	$(CC) $(CFLAGS) $< -L. -lprismc $(LDFLAGS) -o $@

# ── Tests ─────────────────────────────────────────────────────────────────────
test: lib $(TEST_BINS)

$(TEST_DIR)/%: $(TEST_DIR)/%.c
	$(CC) $(CFLAGS) $< -L. -lprismc $(LDFLAGS) -o $@

# ── Install / Uninstall (called by install.sh) ────────────────────────────────
install: lib
	install -m 755 $(LIBRARY) $(OUT_DIR)
	install -m 644 $(INC_DIR)/prismc.h $(HDR_DIR)
	ldconfig

uninstall:
	rm -f $(OUT_DIR)/$(LIBRARY)
	rm -f $(HDR_DIR)/prismc.h
	ldconfig

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -f $(LIB_OBJS) $(LIBRARY) $(EX_BINS) $(TEST_BINS)
