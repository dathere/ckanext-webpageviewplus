[![Tests](https://github.com/dathere/ckanext-webpageviewplus/workflows/Tests/badge.svg?branch=main)](https://github.com/dathere/ckanext-webpageviewplus/actions)

# ckanext-webpageviewplus

An enhanced web page resource view for CKAN. It embeds a web page in an
`<iframe>`, like CKAN core's `webpage_view`, but under its own view type
(`webpage_view_plus`) so both can be enabled side by side.

**TODO:** Document the "plus" features as they are added.


## Requirements

Compatibility with core CKAN versions:

| CKAN version    | Compatible?   |
| --------------- | ------------- |
| 2.9 and earlier | no            |
| 2.10            | not yet tested|
| 2.11            | yes           |


## Installation

**TODO:** Add any additional install steps to the list below.
   For example installing any non-Python dependencies or adding any required
   config settings.

To install ckanext-webpageviewplus:

1. Activate your CKAN virtual environment, for example:

     . /usr/lib/ckan/default/bin/activate

2. Clone the source and install it on the virtualenv

    git clone https://github.com/dathere/ckanext-webpageviewplus.git
    cd ckanext-webpageviewplus
    pip install -e .
	pip install -r requirements.txt

3. Add `webpage_view_plus` to the `ckan.plugins` setting in your CKAN
   config file (by default the config file is located at
   `/etc/ckan/default/ckan.ini`).

4. Restart CKAN. For example if you've deployed CKAN with Apache on Ubuntu:

     sudo service apache2 reload


## Config settings

None at present

**TODO:** Document any optional config settings here. For example:

	# The minimum number of hours to wait before re-checking a resource
	# (optional, default: 24).
	ckanext.webpageviewplus.some_setting = some_default_value


## Developer installation

To install ckanext-webpageviewplus for development, activate your CKAN virtualenv and
do:

    git clone https://github.com/dathere/ckanext-webpageviewplus.git
    cd ckanext-webpageviewplus
    pip install -e .
    pip install -r dev-requirements.txt


## Tests

To run the tests, do:

    pytest --ckan-ini=test.ini

`test.ini` inherits from `/srv/app/src/ckan/test-core.ini`, the path in the
`ckan/ckan-dev` Docker images used by CI. Elsewhere, edit its `use = config:` line
to point at your CKAN checkout's `test-core.ini`.


## Releasing a new version of ckanext-webpageviewplus

If ckanext-webpageviewplus should be available on PyPI you can follow these steps to publish a new version:

1. Update the version number in the `pyproject.toml` file. See [PEP 440](http://legacy.python.org/dev/peps/pep-0440/#public-version-identifiers) for how to choose version numbers.

2. Make sure you have the latest version of necessary packages:

    pip install --upgrade setuptools wheel twine

3. Create a source and binary distributions of the new version:

       python -m build && twine check dist/*

   Fix any errors you get.

4. Upload the source distribution to PyPI:

       twine upload dist/*

5. Commit any outstanding changes:

       git commit -a
       git push

6. Tag the new release of the project on GitHub with the version number from
   the `setup.py` file. For example if the version number in `setup.py` is
   0.0.1 then do:

       git tag 0.0.1
       git push --tags

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)
