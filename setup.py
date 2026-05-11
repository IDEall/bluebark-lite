from setuptools import setup, Extension
from Cython.Build import cythonize

extensions = [
    Extension(
        "bluebark_lite",
        ["bluebark_lite.pyx"],
        extra_compile_args=[
            "-O3",
            "-mcpu=cortex-a53",
            "-ffast-math",
            "-ftree-vectorize",
            "-fomit-frame-pointer"
        ],
        extra_link_args=[
            "-flto"
        ],
    )
]

setup(
    name="BlueBark  DSP Lite",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True
        }
    ),
)