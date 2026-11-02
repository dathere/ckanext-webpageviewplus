# -*- coding: utf-8 -*-
from setuptools import setup

# Note: Do not add new arguments to setup(), instead add setuptools
# configuration options to setup.cfg, or any other project information
# to pyproject.toml
# See https://github.com/ckan/ckan/issues/8382 for details

setup(
    # If you are changing from the default layout of your extension, you may
    # have to change the message extractors, you can read more about babel
    # message extraction at
    # http://babel.pocoo.org/docs/messages/#extraction-method-mapping-and-configuration
    # Extractors as module:function: Babel looks up bare names ("python",
    # "ckan") through pkg_resources, which setuptools is removing, and without
    # it a name like "ckan" is no longer found.
    message_extractors={
        'ckanext': [
            # The vendored PDF.js viewer has its own translations.
            ('**/pdfjs/**', 'ignore', None),
            ('**.py', 'babel.messages.extract:extract_python', None),
            ('**.js', 'babel.messages.extract:extract_javascript', None),
            ('**/templates/**.html', 'ckan.lib.extract:extract_ckan', None),
        ],
    }
)
