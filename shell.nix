{ pkgs ? import <nixpkgs> {} }:

let
  python-env = pkgs.python3.withPackages (ps: with ps; [
    requests
    pandas
    numpy
    matplotlib
    scipy
    scikit-learn
    websockets
    python-dotenv
    aiohttp
  ]);
in
pkgs.mkShell {
  packages = [
    python-env
  ];

  shellHook = ''
    echo "PolyMarket Data Science Environment"
    echo "Python version: $(python --version)"
    echo "Available packages: requests, pandas, numpy, matplotlib, scipy, sklearn"
  '';
}
