/* Appended to the pinned SQLite shim by prepare.py. The existing handle table
   validates the connection; the hook records frame counts without doing I/O. */
static _Thread_local int campfire_wal_frames;
static int campfire_note_wal(void *context, sqlite3 *db, const char *name, int pages) {
    (void)context; (void)db; (void)name;
    campfire_wal_frames = pages;
    return SQLITE_OK;
}
int32_t campfire_track_wal(int64_t handle) {
    sqlite3 *db = db_of(handle);
    if (!db) return SQLITE_MISUSE;
    sqlite3_wal_hook(db, campfire_note_wal, NULL);
    return SQLITE_OK;
}
int64_t campfire_wal_pages(void) { return campfire_wal_frames; }

/* SQLite's WAL index has two 48-byte WalIndexHdr copies. The writer publishes
   the second, then the first; readers accept matching copies after a barrier.
   These functions only certify a cache populated by a completed read transaction.
   An unavailable, changing, or unknown header always takes the SQL path. */
#if defined(__GNUC__) && !defined(_WIN32)
#include <time.h>
static _Thread_local struct {
    int64_t handle;
    sqlite3_file *file;
    volatile uint32_t *header;
    uint32_t before[12], cached[12];
    int candidate, valid;
    time_t began, second;
} campfire_snapshot;

static int campfire_header(int64_t handle, uint32_t out[12]) {
    if (campfire_snapshot.handle != handle || !campfire_snapshot.header) {
        campfire_snapshot.valid = 0;
        campfire_snapshot.header = NULL;
        campfire_snapshot.handle = handle;
        sqlite3 *db = db_of(handle);
        sqlite3_file *file = NULL;
        void volatile *mapped = NULL;
        if (!db || sqlite3_file_control(db, "main", SQLITE_FCNTL_FILE_POINTER, &file) != SQLITE_OK || !file)
            return 0;
        const sqlite3_io_methods *io = file->pMethods;
        if (!io || io->iVersion < 2 || !io->xShmMap || !io->xShmBarrier ||
            io->xShmMap(file, 0, 32768, 0, &mapped) != SQLITE_OK || !mapped) return 0;
        campfire_snapshot.file = file;
        campfire_snapshot.header = mapped;
    }
    for (int attempt = 0; attempt < 3; ++attempt) {
        uint32_t other[12];
        for (int i = 0; i < 12; ++i)
            out[i] = __atomic_load_n(campfire_snapshot.header + i, __ATOMIC_ACQUIRE);
        campfire_snapshot.file->pMethods->xShmBarrier(campfire_snapshot.file);
        for (int i = 0; i < 12; ++i)
            other[i] = __atomic_load_n(campfire_snapshot.header + 12 + i, __ATOMIC_ACQUIRE);
        if (memcmp(out, other, sizeof other) == 0) {
            uint32_t a = 0, b = 0;
            for (int i = 0; i < 10; i += 2) {
                a += out[i] + b;
                b += out[i + 1] + a;
            }
            return out[0] == 3007000 && ((unsigned char *)out)[12] == 1 && a == out[10] && b == out[11];
        }
    }
    return 0;
}

int32_t campfire_cache_begin(int64_t handle) {
    campfire_snapshot.valid = 0;
    campfire_snapshot.began = time(NULL);
    campfire_snapshot.candidate = campfire_header(handle, campfire_snapshot.before);
    return !campfire_snapshot.candidate ||
           memcmp(campfire_snapshot.before, campfire_snapshot.cached, sizeof campfire_snapshot.before) != 0;
}

void campfire_cache_confirm(int64_t handle) {
    uint32_t after[12];
    time_t now = time(NULL);
    if (campfire_snapshot.candidate && now == campfire_snapshot.began &&
        campfire_header(handle, after) && memcmp(after, campfire_snapshot.before, sizeof after) == 0) {
        memcpy(campfire_snapshot.cached, after, sizeof after);
        campfire_snapshot.second = now;
        campfire_snapshot.valid = 1;
    }
}

int32_t campfire_cache_current(int64_t handle) {
    uint32_t current[12];
    return campfire_snapshot.valid && campfire_snapshot.handle == handle &&
           time(NULL) == campfire_snapshot.second && campfire_header(handle, current) &&
           memcmp(current, campfire_snapshot.cached, sizeof current) == 0;
}
#else
int32_t campfire_cache_begin(int64_t handle) { (void)handle; return 0; }
void campfire_cache_confirm(int64_t handle) { (void)handle; }
int32_t campfire_cache_current(int64_t handle) { (void)handle; return 0; }
#endif
