# FEMTICPy compatibility boundary

MT2FEMTIC uses a fixture pinned to FEMTICPy commit
`1b0fe316c01714d06a1c3be2bef5fa47daa67f0c` as an independent compatibility
reference. FEMTICPy is not a runtime dependency, and this gate does not claim
byte-for-byte equivalence between the two programs.

## Agreements

- `(mV/km)/nT` impedance values use the factor `1000 * mu0` to obtain ohms.
- An explicitly configured `exp(+i*omega*t)` source is conjugated once to the
  internal `exp(-i*omega*t)` convention.
- The impedance floor is the declared fraction times the geometric mean of
  the off-diagonal impedance magnitudes.
- VTF errors use the declared absolute floor.
- EDI station identity, geographic coordinates, elevation, frequencies, and
  response components are parsed consistently for the pinned fixture.

## Intentional policy differences

MT2FEMTIC requires the time convention to be present in source metadata or to
be explicitly overridden in configuration. The pinned FEMTICPy fixture omits
that metadata, so the compatibility test records an `exp_plus_iwt` override.
MT2FEMTIC also makes frequency selection explicit as `exact` or `log_linear`,
preserves stable natural station order, and hashes every source and output.

## File-contract differences

The external FEMTIC reader accepts official four-column station headers,
five-column headers with one individual selector, and six-column MT headers
with both individual selectors. The MT2FEMTIC writer emits one canonical form:
five columns for both MT and VTF station headers, with lower-element selector
`0` for MT, upper-element selector `1` for VTF, and model X/Y coordinates in
the final two columns.

## Excluded scope

FEMTICPy notebook state, TetGen, `makeTetraMesh`, tetrahedral mesh scripts, and
unvalidated post-inversion result parsing are outside the current DHEXA-only
release.
