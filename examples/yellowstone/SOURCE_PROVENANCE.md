# Yellowstone source provenance

- Study: Kelbert, Egbert, and deGroot-Hedlin (2012), *Crust and upper mantle
  electrical conductivity beneath the Yellowstone Hotspot Track*, *Geology*,
  40(5), 447-450.
- Article DOI: <https://doi.org/10.1130/G32655.1>.
- Supplemental material DOI: <https://doi.org/10.1130/2012118>.
- EarthScope USArray EMTF survey DOI:
  <https://doi.org/10.17611/DP/EMTF/USARRAY/TA>.
- EarthScope EMTF product: <https://ds.iris.edu/ds/products/emtf/>.
- Archive status reported for all 98 selected files: Unrestricted Release;
  data citation is required.

`source-files.json` records the authoritative download URL, byte count, and
SHA-256 for each EDI file checked on 2026-09-22. `edi-files.txt` preserves the
retained 98-site order.

The example reproduces the retained station set and global data-preparation
settings against that archive snapshot. It is a 98-site retained subset, not
the complete 123-site data set described by Kelbert et al. (2012). The 5%
impedance and 0.03 final VTF error floors are documented in the paper's
supplement; the 14 exact periods and the 10000 s VTF cutoff come from the
retained input audit.

The zero-azimuth Mercator scale and origin in `survey.json` were recovered by
fitting the retained station-coordinate pairs; they are an explicit workflow
reconstruction, not a projection reported by Kelbert et al. (2012). With the
current EDI coordinates, 97 sites reproduce the retained horizontal positions
within 4.4 m; `NVM11` differs by about 32 m. Topography and observation
refinement are disabled because their original inputs were not found.

A comparison with the retained local inputs found matching selected-period
impedance values for 97 of 98 stations.
The exception is `NVM11`: the retained inventory named a 2011 response, while
the current public catalog supplies the 2010 response. The retained input also
contains quality-control removals not represented by the public EDI metadata:
50 MT station-period rows, 192 additional impedance component masks, 10 VTF
station-period rows, and one additional VTF component mask relative to the
current Stage 03 output.

The old DHEXA `mesh.dat` and initial model are retained validation artifacts,
but their original `meshgen.inp`, topography source, and generator identity are
not available. They are therefore excluded from this public example.
