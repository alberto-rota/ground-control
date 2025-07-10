from setuptools import setup, find_packages

setup(
    name="groundcontrol",
    version="v1.0.2",
    packages=find_packages(),
    install_requires=[
        "click==8.2.1",
        "numpy==1.24.3", 
        "nvitop==1.5.1",
        "platformdirs==4.3.8",
        "plotext==5.3.2",
        "psutil==7.0.0",
        "pynvml==12.0.0",
        "setuptools==65.6.3",
        "textual==3.7.1",
    ],
    entry_points={
        "console_scripts": [
            "groundcontrol = ground_control.main:entry",
        ],
    },

    description="A Python Textual app for monitoring VMs in the terminal",
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
