-- brand-logo.png (kapacitet/brand.py): the DSO's logo on the applicant's portal, already cleaned
-- to its IHDR, PLTE, tRNS, IDAT and IEND chunks; the seal chain keeps its digest, never its bytes.
CREATE TABLE brand_logo (
    scope      text        NOT NULL PRIMARY KEY,
    png        bytea       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
