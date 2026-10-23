# Licensing

## This integration

The eBUS integration (`ebus`) is **Apache-2.0**. It depends on **aioebus**
(Apache-2.0), a pure
Python eBUS library that contains no device data.

## Device definitions

The definitions shipped in `custom_components/ebus/configs/` are **our own,
independently created** work (currently the eloBlock and the NETX3), built from
our own bus captures and device identification — not copied from a third party.
They are Apache-2.0.

## Why not the full ebusd-configuration set

The obvious source for broad device coverage,
[ebusd-configuration](https://github.com/john30/ebusd-configuration), is
**GPL-3.0**. Converting those definitions to our format yields a GPL-3.0
*derivative* (both the expression — names, comments, selection/arrangement — and,
in the EU, the *sui generis database right* on the maker's substantial
investment apply). A GPL-3.0 derivative cannot be shipped under Apache-2.0, and
**Home Assistant Core requires Apache-2.0** for bundled code and data. So we do
**not** ship anything derived from ebusd-configuration.

## Plan

1. **Requested** a permissive / dual-license grant (Apache-2.0 or MIT) for the
   configuration data from the author (John Baier), with attribution and
   upstream contribution back, and an offer to co-maintain. *(email sent)*
2. **If granted:** convert the full set into our format and ship it under
   Apache-2.0 with a `NOTICE` crediting ebusd-configuration / John Baier.
3. **If declined / meanwhile:** two Apache-clean options —
   - **local conversion:** users run `python -m aioebus.convert` against *their
     own* copy of ebusd-configuration; the derivative stays on their machine and
     is never distributed by us (GPL triggers on distribution, not private use);
   - **clean-room:** grow our own definitions from independent sources (own
     captures, Vaillant service documentation, the eBUS specification).

## Note on git history

Earlier commits in these (private) repositories contained definitions converted
from ebusd-configuration. They have been removed from the working tree. Before
any public release without a license grant, the history should be rewritten to
drop them (or the grant makes this moot).
