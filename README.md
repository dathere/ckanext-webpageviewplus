[![Tests](https://github.com/dathere/ckanext-webpageviewplus/workflows/Tests/badge.svg?branch=main)](https://github.com/dathere/ckanext-webpageviewplus/actions)

# ckanext-webpageviewplus

An enhanced web page resource view for CKAN. It embeds a web page in an
`<iframe>`, like CKAN core's `webpage_view`, but under its own view type
(`webpage_view_plus`) so both can be enabled side by side.



## Requirements

Compatibility with core CKAN versions:

| CKAN version    | Compatible?   |
| --------------- | ------------- |
| 2.9 and earlier | no            |
| 2.10            | yes           |
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


## Usage

Add a "Website (Plus)" view to an HTML resource and pick a security preset:

| Preset           | Sandbox                                         | Use for |
| ---------------- | ----------------------------------------------- | ------- |
| `strict`         | everything restricted, no scripts               | static HTML |
| `interactive`    | `allow-scripts allow-popups allow-downloads`    | scripted pages that need no storage (default) |
| `external`       | none (needs a content origin)                   | trusted third-party sites; runs as `interactive` until content-origin support lands, and is never allowed for pages on CKAN's own host |
| `data-schematic` | adds `allow-same-origin` (needs a content origin) | qsv Data Schematics; runs as `interactive` until content-origin support lands |

Until uploaded HTML is served from a separate content origin, no frame may let scripts keep
a real origin: such a page could navigate itself to an HTML file uploaded to CKAN and run it
as CKAN. So `external` runs as `interactive`, and a sandbox combining `allow-scripts` with
`allow-same-origin` is refused.

Every frame gets `loading="lazy"`, a `referrerpolicy`, the view's title as its accessible
name, and an `allow` list that always denies ad and tracking features. Sysadmins can also set
sandbox tokens, allowed features and the referrer policy per view.

To convert existing `webpage_view` views (always start with `--dry-run`, and back up the
database first: the command rewrites views in place):

    ckan -c /etc/ckan/default/ckan.ini webpageviewplus migrate --dry-run
    ckan -c /etc/ckan/default/ckan.ini webpageviewplus migrate [--uploads-only] [--preset NAME]

**If your site embeds third-party apps or dashboards, migrate with `--uploads-only` for now.**
Stock `webpage_view` runs embedded pages unsandboxed. Until content-origin support lands,
`external` runs as `interactive`, so a migrated third-party app loses its own logins and
saved settings. `--uploads-only` converts just the views of uploaded HTML. Those gain the
most, since stock `webpage_view` runs them unsandboxed on CKAN's own origin. Linked pages
stay on `webpage_view`; keep both plugins enabled.


## Config settings

All optional. See `ckanext/webpageviewplus/config_declaration.yaml` for details.

| Key | Default | Meaning |
| --- | ------- | ------- |
| `ckanext.webpageviewplus.default_preset` | `interactive` | Preset for new views |
| `ckanext.webpageviewplus.presets` | all four | Presets editors may choose from |
| `ckanext.webpageviewplus.editor_can_loosen` | `false` | Let editors (not only sysadmins) set sandbox, allow and referrer policy |
| `ckanext.webpageviewplus.referrer_policy` | `strict-origin-when-cross-origin` | Default `referrerpolicy` |
| `ckanext.webpageviewplus.min_height` / `max_height` | `400` / `4000` | Clamp for viewport and auto heights, in px |
| `ckanext.webpageviewplus.site_aliases` | empty | Other hosts this CKAN is reachable under; treated as CKAN's own |
| `ckanext.webpageviewplus.allowed_hosts` | empty (any) | Allow-list of hosts an external page URL may point to |
| `ckanext.webpageviewplus.formats` | `html htm` | Resource formats the view is offered for |


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
