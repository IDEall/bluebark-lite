# bluebark_lite.pyx v0.112

import time

cimport cython
from libc.stdio cimport FILE, fclose, fopen, fread


cdef float NO_DATA_VALUE = -99.0
cdef float NOISE_FLOOR_ESTIMATE = -46.0
cdef float SHARPNESS_DELTA_DB = 3.5
cdef int MAX_SWEEP_BINS = 512


cdef class SharedState:
    cdef public float max_val
    cdef public float floor_db
    cdef public bint shutdown
    cdef public int bins
    cdef public int offset

    def __init__(self, float floor_db, int bins, int offset):
        self.max_val = NO_DATA_VALUE
        self.floor_db = floor_db
        self.shutdown = False
        self.bins = bins
        self.offset = offset

    cpdef void update_peak(self, float value):
        if value > self.max_val:
            self.max_val = value

    cpdef float consume_peak(self):
        cdef float value = self.max_val
        self.max_val = NO_DATA_VALUE
        return value


cdef float c_sweep[512]


cdef inline void find_peak_and_index(
    const float* data,
    int num_elements,
    float* out_max,
    int* out_index,
) nogil:
    cdef int idx
    cdef int best_index = 0
    cdef float best_value = NO_DATA_VALUE

    for idx in range(num_elements):
        if data[idx] > best_value:
            best_value = data[idx]
            best_index = idx

    out_max[0] = best_value
    out_index[0] = best_index


cdef inline float apply_sharpness_filter(
    const float* data,
    int expected_bins,
    int peak_index,
    int offset,
    float peak_value,
) nogil:
    cdef float left_val
    cdef float right_val

    if peak_value <= NOISE_FLOOR_ESTIMATE:
        return peak_value

    if offset <= 0:
        return peak_value

    if peak_index < offset or peak_index >= (expected_bins - offset):
        return peak_value

    left_val = data[peak_index - offset]
    right_val = data[peak_index + offset]

    if (peak_value - left_val < SHARPNESS_DELTA_DB) and (peak_value - right_val < SHARPNESS_DELTA_DB):
        return NOISE_FLOOR_ESTIMATE

    return peak_value


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def c_sdr_data_thread(SharedState state):
    cdef FILE* c_fifo = NULL
    cdef size_t bytes_read = 0
    cdef int expected_bins
    cdef int offset
    cdef int peak_index
    cdef float current_max

    while not state.shutdown:
        with nogil:
            c_fifo = fopen("/dev/shm/tetra.bin", "rb")

        if c_fifo == NULL:
            time.sleep(0.02)
            continue

        while not state.shutdown:
            expected_bins = state.bins
            offset = state.offset

            if expected_bins <= 0 or expected_bins > MAX_SWEEP_BINS:
                time.sleep(0.001)
                continue

            with nogil:
                bytes_read = fread(&c_sweep[0], cython.sizeof(c_sweep[0]), expected_bins, c_fifo)
                if bytes_read == <size_t>expected_bins:
                    find_peak_and_index(&c_sweep[0], expected_bins, &current_max, &peak_index)
                    current_max = apply_sharpness_filter(
                        &c_sweep[0],
                        expected_bins,
                        peak_index,
                        offset,
                        current_max,
                    )

            if bytes_read < <size_t>expected_bins:
                break

            state.update_peak(current_max)

        with nogil:
            if c_fifo != NULL:
                fclose(c_fifo)
                c_fifo = NULL
