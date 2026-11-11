# Changelog

## 1.0.1

- Manifest: reference links moved from the deprecated `artifacts:` key to `links:`.
- A cached comparison is only served to a caller who may open the other log (404 otherwise), so a shared log's viewer cannot read the owner's runs against unshared logs.
