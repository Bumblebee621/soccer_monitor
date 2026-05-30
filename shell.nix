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
    aiogram
    yfinance
    apscheduler
    lxml
  ]);
in
pkgs.mkShell {
  packages = [
    python-env
  ];

  shellHook = ''
    echo "PolyMarket Data Science Environment"
    echo "Available packages: requests, pandas, numpy, matplotlib, scipy, sklearn"
  '';
}
