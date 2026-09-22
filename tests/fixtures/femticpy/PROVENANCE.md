# FEMTICPy compatibility fixture provenance

- Upstream repository: <https://github.com/hseille/femticPy>
- Upstream commit: `1b0fe316c01714d06a1c3be2bef5fa47daa67f0c`
- Original path: `projects/synthetic/input_data/edi_files/01.edi`
- Local path: `synthetic_01.edi`
- Retrieval date: 2026-09-14
- SHA-256: `8b931877321e1c3e191656ece808a8125056d3f3c07eaa379cc3547f8c6c68c0`
- Upstream license: MIT; a verbatim copy is stored as `LICENSE` in this directory.

The upstream EDI file does not declare its Fourier sign convention in file
metadata. The compatibility test therefore records an explicit
`exp_plus_iwt` user override and verifies the resulting conversion to the
MT2FEMTIC internal `exp_minus_iwt` convention. This is a test policy, not a
claim that the EDI file itself declares the convention.
