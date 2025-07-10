from setuptools import setup, find_packages

setup(
    name="ground-control-tui",
    version="v1.2.0",
    packages=find_packages(),
    install_requires=[
        "numpy==2.0.0",
        "nvidia_ml_py==12.570.86", 
        "nvitop==1.4.2",
        "platformdirs==4.3.6",
        "plotext==5.3.2",
        "psutil==6.1.1",
        "pynvml==11.5.3",
        "setuptools==75.1.0",
        "textual==1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "groundcontrol = ground_control.main:entry",
            "gc = ground_control.main:entry",
        ],
    },

    description="A Python Textual app for monitoring hardware in the terminal",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/alberto-rota/ground-control",
    author="Alberto Rota", 
    author_email="alberto1.rota@polimi.it",
    license="GPLv3",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
