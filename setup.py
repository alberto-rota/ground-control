from setuptools import setup, find_packages

setup(
    name="ground-control-tui",
    version="0.1.9",
    packages=find_packages(),
    install_requires=[
        "nvidia_ml_py==12.535.161",
        "plotext==5.3.2",
        "psutil==6.0.0",
        "setuptools==75.1.0",
        "textual==0.89.1",
    ],  
    entry_points={
        "console_scripts": [
            "groundcontrol = ground_control.main:main",  # Change main to your entry function
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
