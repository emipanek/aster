'''
This code automatically downloads the requisite line-lists

We reccommend the following sources, but you can add more:

Opacities:
 - H2O: https://www.exomol.com/db/H2O/1H2-16O/POKAZATEL/1H2-16O__POKAZATEL__R15000_0.3-50mu.xsec.TauREx.h5
 - CO2: https://www.exomol.com/db/CO2/12C-16O2/Dozen/12C-16O2__Dozen.R15000_0.3-50mu.xsec.TauREx.h5
 - NH3: https://www.exomol.com/db/NH3/14N-1H3/CoYuTe/14N-1H3__CoYuTe.R15000_0.3-50mu.xsec.TauREx.h5
 - CH4: https://www.exomol.com/db/CH4/12C-1H4/MM/12C-1H4__MM.R15000_0.3-50mu.xsec.TauREx.h5
 - CO:  https://www.exomol.com/db/CO/12C-16O/Li2015/C-O-NatAbund__Li2015.R15000_0.3-50mu.xsec.TauREx.h5

## CIA
 - H2-H2: https://hitran.org/data/CIA/main/H2-H2_2011.cia
 - H2-He: https://hitran.org/data/CIA/main/H2-He_2011.cia
'''

from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

LINE_LIST_PATH = Path("workspace/linelists")
XSEC_DIR = LINE_LIST_PATH / "xsec"
CIA_DIR = LINE_LIST_PATH / "cia"

OPACITIES = {
    "H2O": "https://www.exomol.com/db/H2O/1H2-16O/POKAZATEL/1H2-16O__POKAZATEL__R15000_0.3-50mu.xsec.TauREx.h5",
    "CO2": "https://www.exomol.com/db/CO2/12C-16O2/Dozen/12C-16O2__Dozen.R15000_0.3-50mu.xsec.TauREx.h5",
    "NH3": "https://www.exomol.com/db/NH3/14N-1H3/CoYuTe/14N-1H3__CoYuTe.R15000_0.3-50mu.xsec.TauREx.h5",
    "CH4": "https://www.exomol.com/db/CH4/12C-1H4/MM/12C-1H4__MM.R15000_0.3-50mu.xsec.TauREx.h5",
    "CO":  "https://www.exomol.com/db/CO/12C-16O/Li2015/C-O-NatAbund__Li2015.R15000_0.3-50mu.xsec.TauREx.h5",
}

CIA = {
    "H2-H2": "https://hitran.org/data/CIA/main/H2-H2_2011.cia",
    "H2-He": "https://hitran.org/data/CIA/main/H2-He_2011.cia",
}


def filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def download(url: str, out_dir: Path, *, overwrite: bool = False, timeout_s: int = 60) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = filename_from_url(url)
    dest = out_dir / fname

    if dest.exists() and not overwrite:
        print(f"Skipping (exists): {dest}")
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")

    with requests.get(url, stream=True, timeout=timeout_s) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None

        desc = fname if len(fname) <= 60 else (fname[:27] + "..." + fname[-30:])
        with tmp.open("wb") as f, tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=desc,
            leave=True,
        ) as pbar:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                pbar.update(len(chunk))

    tmp.replace(dest)
    return dest


def main() -> None:
    print("\nStarting download of line-lists. This may take several minutes...")
    
    for label, url in OPACITIES.items():
        print(f"\nOpacity: {label}")
        download(url, XSEC_DIR)

    for label, url in CIA.items():
        print(f"\nCIA: {label}")
        download(url, CIA_DIR)

    print("\nAll done :)\n")


if __name__ == "__main__":
    main()
