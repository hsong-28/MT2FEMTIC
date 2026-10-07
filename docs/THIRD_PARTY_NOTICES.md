# Third-party notices

## FEMTICPy compatibility fixture

MT2FEMTIC includes one EDI test fixture copied from FEMTICPy:

- Project: FEMTICPy
- Repository: <https://github.com/hseille/femticPy>
- Copyright: FEMTICPy contributors
- Commit: `1b0fe316c01714d06a1c3be2bef5fa47daa67f0c`
- Original file: `projects/synthetic/input_data/edi_files/01.edi`
- Included file: `.github/tests/fixtures/femticpy/synthetic_01.edi`
- License: MIT
- License copy: `.github/tests/fixtures/femticpy/LICENSE`

No FEMTICPy source code is included or imported at runtime. See the
[fixture provenance and compatibility boundary](../.github/tests/fixtures/femticpy/PROVENANCE.md).

## Jif3D conversion fixture

`.github/tests/fixtures/jif3d` contains Jif3D's `testJ.j` and `testJ.mtt` samples
and NetCDF fixtures generated with its native I/O functions. The upstream
distribution supplies GPL v3; its license is retained in that directory.
See the [fixture provenance](../.github/tests/fixtures/jif3d/README.md) for sources,
hashes, preparation, and validation scope. No Jif3D source code or executable
is included or imported at runtime.

## Example data and mesh generators

- Broken Hill data: CC BY 4.0; see
  [source provenance](../examples/broken_hill/SOURCE_PROVENANCE.md).
- Bundled DHEXA executables: see
  [license text](../examples/broken_hill/tools/LICENSE.makeDHexaMesh) and the
  executable identities in the Broken Hill provenance record.
- Yellowstone: data are downloaded separately; see
  [source provenance](../examples/yellowstone/SOURCE_PROVENANCE.md).
