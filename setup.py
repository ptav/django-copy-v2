#!/usr/bin/env python
import os
from setuptools import setup,find_packages

PATH = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(PATH, 'README.md'), encoding='utf-8') as fp:
    DESC = fp.read()


setup(
    name='django-copy',
    version='0.3.2',
    description='Probably the smallest and simplest CMS for the Django framework',
    long_description=DESC,
    long_description_content_type = 'text/markdown',
    keywords='Django, CMS',
    author='Pedro Tavares',
    author_email='web@ptavares.com',
    url='https://github.com/ptav/django-copy',
    license='LICENSE',

    packages=find_packages(),
    include_package_data=True,

    python_requires='>=3.10',
    install_requires=[
        'Django>=5.2.17,<6.1',
        'django-simple-history>=3.13.0',
        'django-summernote>=0.8.20.0',
        'django-filer>=3.5.0',
        'django-bootstrap5>=26.2',
        'user-agents>=2.2.0',
        'html2text>=2025.4.15',
        'Markdown>=3.10.3',
        'geoip2>=5.3.0',
    ],
    
    classifiers=[
        "Programming Language :: Python",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Framework :: Django",
        "Environment :: Web Environment",
        "Development Status :: 4 - Beta",
        "Programming Language :: Python",
        "Framework :: Django :: 5.2",
        "Framework :: Django :: 6.0",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
)
