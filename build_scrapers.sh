#!/bin/bash
# AGPLv3.0
# https://github.com/stashapp/CommunityScripts/blob/main/LICENSE

# builds a repository of scrapers
# outputs to _site/scrapers with the following structure:
# index.yml
# <scraper_id>.zip
# Each zip file contains the scraper .yml file and any other files in the same directory

outdir="$1"
if [ -z "$outdir" ]; then
    outdir="_site/scrapers/main"
fi

rm -rf "$outdir"
mkdir -p "$outdir"

buildScraper()
{
    f=$1
    # get the scraper id from the directory
    dir=$(dirname "$f")
    scraper_id=$(basename "$f" .yml)

    echo "Processing $scraper_id"

    # get git metadata
    version=$(git log -n 1 --pretty=format:%h -- "$dir"/*)
    updated=$(TZ=UTC0 git log -n 1 --date="format-local:%F %T" --pretty=format:%ad -- "$dir"/*)

    # create the zip file
    zipfile=$(realpath "$outdir/$scraper_id.zip")

    pushd "$dir" > /dev/null
    zip -r "$zipfile" . > /dev/null
    popd > /dev/null

    name=$(grep "^name:" "$f" | head -n 1 | cut -d' ' -f2- | sed -e 's/\r//' -e 's/^"\(.*\)"$/\1/')

    # write to spec index
    echo "- id: $scraper_id
  name: $name
  version: $version
  date: $updated
  path: $scraper_id.zip
  sha256: $(sha256sum "$zipfile" | cut -d' ' -f1)" >> "$outdir"/index.yml

    echo "" >> "$outdir"/index.yml
}

find ./scrapers -mindepth 1 -name *.yml | while read file; do
    buildScraper "$file"
done
