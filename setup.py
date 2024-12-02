from setuptools import setup, find_packages

setup(
    name="ground-control",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[],  # Add dependencies listed in requirements.txt
    entry_points={
        "console_scripts": [
            "ground-control = ground_control.monitor:main",  # Change main to your entry function
        ],
    },
    description="A Python Textual app for monitoring VMs in the terminal",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/alberto-rota/ground-control",
    author="Your Name",
    author_email="your_email@example.com",
    license="MIT",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
