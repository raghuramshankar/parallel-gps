import setuptools

with open("requirements2.txt") as f:
    requirements = f.read().splitlines()

setuptools.setup(
    name="pssgp",
    install_requires=requirements,
    author="Adrien Corenflos, Zheng Zhao",
    packages=setuptools.find_packages(
        where="pssgp",
        exclude=[
            "toymodels",
            "experiments",
            "experiments.sunspot",
            "experiments.toy_models",
            "experiments.co2",
        ],
    ),
    package_dir={
        "pssgp": "pssgp",
        "kalman": "pssgp/kalman",
        "kernels": "pssgp/kernels",
    },
    version="1.0.0",
)
