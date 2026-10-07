# FEMTICPy compatibility fixture provenance

- Upstream repository: <https://github.com/hseille/femticPy>
- Upstream commit: `1b0fe316c01714d06a1c3be2bef5fa47daa67f0c`
- Original path: `projects/synthetic/input_data/edi_files/01.edi`
- Local path: `synthetic_01.edi`
- Retrieval date: 2026-09-14
- SHA-256: `8b931877321e1c3e191656ece808a8125056d3f3c07eaa379cc3547f8c6c68c0`
- Upstream license: MIT; a verbatim copy is stored as `LICENSE` in this directory.

This pinned fixture is an independent compatibility reference. FEMTICPy is
not a runtime dependency; the gate does not claim byte-for-byte equivalence
between the two programs.

## Agreements

- `(mV/km)/nT` impedance values use the factor `1000 * mu0` to obtain ohms.
- An explicitly configured `exp(+i*omega*t)` source is conjugated once to the
  internal `exp(-i*omega*t)` convention.
- The impedance floor is the declared fraction times the geometric mean of
  the off-diagonal impedance magnitudes.
- VTF errors use the declared absolute floor.
- EDI station identity, geographic coordinates, elevation, frequencies, and
  response components are parsed consistently for this fixture.

## Intentional policy differences

MT2FEMTIC requires a time convention in source metadata or an explicit
configuration override. The fixture omits that metadata, so the test records
an `exp_plus_iwt` override; this is a test policy, not a convention declared
by the file. Frequency selection is explicit as `exact` or `log_linear`;
MT2FEMTIC preserves stable natural station order and hashes source and output.

## File-contract differences

The reader accepts four- or five-column station headers and six-column MT
headers. The writer emits five columns, with lower-element selector `0` for
MT, upper-element selector `1` for VTF, and model X/Y as the final two columns.
See the [FEMTIC input contract](../../../../docs/femtic-input-contract.md).

## Excluded scope

FEMTICPy notebook state, TetGen, `makeTetraMesh`, tetrahedral mesh scripts, and
unvalidated post-inversion result parsing are outside this DHEXA-only release.
