# Mammal trait list

The mammal trait list is the thesaurus behind `traitName`, `traitID`, `traitValue` and `traitUnit` in an ETS row. The extractor stores only an internal `traitCode` such as `body_mass`. The ETS adapter ([ets-adapter.md](ets-adapter.md)) looks the code up here to get the name, the identifier and the standard unit, and to convert the printed value.

The list is one JSON file, `src/bioparser/traits/mammal_traits.json`. It is data, so a trait is added by editing the file. The models in `src/bioparser/traits/table.py` validate it.

The maintained set is a generic base. It has 39 traits in six domains. A trait belongs in the base when it is generic across mammals and the common trait databases carry it. Traits that only some papers print are left to dynamic traits, which are added later on top of the base.

## The file

| Key | What it holds |
|---|---|
| `version` | `YYYY.MM.REVISION`. The year and month the list was named, then a revision that starts at 0. |
| `id_prefixes` | Each identifier prefix and its address, or `null` when the prefix does not resolve yet. |
| `unit_aliases` | Spellings of a unit that every numeric trait accepts. |
| `level_aliases` | Printed answers that every categorical trait accepts for a level. |
| `domains` | The buckets traits are grouped in. A name and a definition each. |
| `traits` | The traits. |

## Traits

Every trait has a `code`, a `name`, a `trait_id`, a `definition`, a `domain` and a `kind`.

- The `code` is lowercase words joined by underscores. It is what the extractor stores.
- The `trait_id` is a prefix, a colon and seven digits, such as `VT:0001259`. The prefix needs an entry in `id_prefixes`.
- The `definition` stands on its own. It names the measuring points and does not refer to other traits or to later work.
- A domain definition is a bucket, such as "Reproduction, development and lifespan". It names the kind of thing, not each trait.

There are three kinds.

| Kind | Holds | Extra fields |
|---|---|---|
| `numeric` | A measured quantity, converted into one unit. | `trait_unit`, `printed_units`, `approximate_units`, `unit_aliases` |
| `count` | A number of things, such as young or teats. It is not converted. | `trait_unit`, `unit_aliases` |
| `categorical` | One of a closed set of levels. It has no unit. | `levels`, `level_aliases` |

A count is a whole number, unless the paper prints a mean or a median. The row's `aggregateMeasure` says which. The trait does not say whether a value is a mean.

## Units

A numeric trait has a `trait_unit`. That is the one unit its values are stored in, and it is what ends up in `traitUnit`. `printed_units` maps each unit a paper may print to the factor that converts it into the trait unit. The trait unit is itself a printed unit with factor 1. A factor is a positive decimal written as text, such as `"0.0201"`.

Only a unit whose conversion is exact should have an exact factor. A unit whose conversion is a convention, such as a year of 365.25 days, goes in `approximate_units`. A row converted from it carries an `approximate` warning.

The trait unit is chosen once. Days suit gestation and weaning because the conversions from weeks, months and years all terminate. Years suit maximum longevity because papers print years. Each trait has its own trait unit.

### How a printed unit is matched

A printed unit is cleaned first. Spaces, a trailing period, a `^` or a superscript, and a negative exponent are normalized, so `individuals ha-1` and `individuals ha⁻¹` both become `individuals/ha`. A period at the end of either side of a `/` is removed, so `ind. ha-1` becomes `ind/ha`. Then it is matched in two passes.

1. **With its case.** `mg` is a milligram and `Mg` is a tonne. They are different units here.
2. **Without its case**, only if the first pass found nothing. `Kg` and `KG` still mean `kg`.

An alias is skipped in the second pass when its case-folded spelling is a printed unit of the trait, or when two aliases fold to the same spelling with different targets. That keeps `Mg` from turning `MG` into a tonne.

A `/` splits a unit into sides, and each side is looked up on its own. So `yr` in `litters/yr` is read as `year`.

### Aliases

A spelling that cleaning cannot reach is an alias, such as `lbs` for `lb` or `deg C` for `°C`. Aliases are part of the list, so the list says everything it accepts.

- `unit_aliases` at the top of the file applies to every numeric and count trait. A list-wide alias may name a unit no trait in the file prints. A subset of the traits still validates.
- `unit_aliases` on a trait applies to that trait only. The target must be one of its printed units. It wins over the list-wide alias for the same spelling.
- An alias of a unit to itself turns the list-wide alias off for that trait. A trait that prints `C` for something other than temperature can do this.

A unit that some trait accepts but this one does not warns `unit_not_for_trait`. A numeric trait warns `unit_unknown` when no trait accepts the unit, and the row keeps the printed value and unit. A count or a level keeps its value when the unit is one nobody accepts.

## Levels

A categorical trait has two or more `levels`. Matching ignores case and separators, so `Nocturnal/Crepuscular` and `nocturnal crepuscular` meet.

`level_aliases` map a printed form to a level, in the same two places as unit aliases.

- At the top of the file, an alias such as `Y` for `yes` applies to every trait that has that level.
- On a trait, an alias applies to that trait only and wins. `hibernation_torpor` maps `y` and `yes` to `torpor`, because a paper that says only "uses torpor" has not said hibernation.
- There is one lookup. A trait alias and a list-wide alias are merged, and the printed form is resolved once.
- A trait alias may not use a level's name for a different level. It may map a level to itself.
- A list-wide alias may not use the name of a trait's level, unless that trait maps the level to itself. Otherwise the alias would redirect a real level.

A printed form that no level or alias matches warns `not_in_vocabulary`, and the row keeps the printed text.

## Identifiers

`trait_id` is a prefix and seven digits. `id_prefixes` lists each prefix with its address, such as `http://purl.obolibrary.org/obo/VT_`, or `null`. A prefix is letters only. An address is `null` or starts with `http://` or `https://`.

The project's own traits use the `BPT` prefix. Its address is `https://bioparser.github.io/BioParser/traits/BPT/`. Traits that match a public ontology term use that term, such as `VT:0001259` for body mass. An identifier may move to a public term.

## The base set

Version 2026.11.0 has 39 traits. The trait unit of a numeric or count trait, or the levels of a categorical one, are shown.

### morphology

| Code | Kind | Unit or levels |
|---|---|---|
| `body_mass` | numeric | g |
| `brain_mass` | numeric | g |
| `head_body_length` | numeric | mm |
| `total_length` | numeric | mm |
| `tail_length` | numeric | mm |
| `hind_foot_length` | numeric | mm |
| `hind_foot_length_with_claw` | numeric | mm |
| `ear_length` | numeric | mm |
| `neonate_mass` | numeric | g |
| `weaning_mass` | numeric | g |
| `neonate_head_body_length` | numeric | mm |
| `weaning_head_body_length` | numeric | mm |

### life_history

| Code | Kind | Unit or levels |
|---|---|---|
| `litter_size` | count | young |
| `litters_per_year` | numeric | litters/year |
| `gestation_length` | numeric | day |
| `weaning_age` | numeric | day |
| `age_at_sexual_maturity` | numeric | day |
| `age_at_first_reproduction` | numeric | day |
| `interbirth_interval` | numeric | day |
| `maximum_longevity` | numeric | year |
| `teat_number` | count | teats |

### ecology

| Code | Kind | Unit or levels |
|---|---|---|
| `activity_cycle` | categorical | nocturnal, nocturnal/crepuscular, crepuscular, diurnal/crepuscular, cathemeral, diurnal |
| `foraging_stratum` | categorical | fossorial, ground, scansorial, arboreal, aerial, aquatic |
| `hibernation_torpor` | categorical | hibernation, torpor, no |
| `migratory_status` | categorical | yes, no, partial |
| `social_group_size` | numeric | individuals |
| `individual_home_range_size` | numeric | km2 |
| `population_density` | numeric | individuals/km2 |

### physiology, distribution and skeleton

| Code | Domain | Kind | Unit |
|---|---|---|---|
| `basal_metabolic_rate` | physiology | numeric | kJ/h |
| `body_temperature` | physiology | numeric | °C |
| `geographic_range_size` | distribution | numeric | km2 |
| `greatest_skull_length` | skeleton | numeric | mm |
| `condylobasal_length` | skeleton | numeric | mm |
| `zygomatic_breadth` | skeleton | numeric | mm |
| `braincase_breadth` | skeleton | numeric | mm |
| `mastoid_breadth` | skeleton | numeric | mm |
| `interorbital_breadth` | skeleton | numeric | mm |
| `maxillary_toothrow_length` | skeleton | numeric | mm |
| `mandible_length` | skeleton | numeric | mm |

Basal metabolic rate is the rate of a whole animal. A rate per gram of body mass has no printed unit here, so the row warns and keeps the printed text.

## Checking a list

`bioparser.traits.table.list_problems(text)` takes the JSON text of a list and returns every problem in it, or an empty list. Each problem has a `rule`, a `trait` code or `None`, the JSON `field`, the `spelling` involved and a sentence in `message`. The sentence names the field in words, such as "unit aliases", and the data fields keep the JSON name.

| Rule | What is wrong |
|---|---|
| `not_json` | The text is not JSON. |
| `invalid_value` | A value has the wrong type or is missing. |
| `invalid_code` | A code is not lowercase words joined by underscores. |
| `empty_text` | A trait or a domain has a name or a definition that is empty, or only spaces. |
| `invalid_trait_id` | A trait id is not a prefix, a colon and seven digits. |
| `undeclared_prefix` | A trait id uses a prefix with no entry in `id_prefixes`. |
| `invalid_prefix`, `invalid_iri` | An entry in `id_prefixes` has a bad prefix or a bad address. |
| `duplicate_code`, `duplicate_id`, `duplicate_domain` | A code, an id or a domain name is used twice. |
| `unknown_domain` | A trait uses a domain the list does not have. |
| `no_printed_units`, `invalid_factor` | A numeric trait has no printed units, or a factor is not a positive decimal. |
| `duplicate_unit` | Two printed units are spellings of the same unit. |
| `trait_unit_not_printed` | The trait unit is not a printed unit with factor 1. |
| `approximate_not_printed` | An approximate unit is not a printed unit. |
| `duplicate_alias` | Two aliases in one table are the same spelling once cleaned. |
| `unit_alias_target_not_printed`, `unit_alias_target_not_trait_unit` | A trait's unit alias names a unit the trait does not print, or a count's alias names another unit. |
| `levels_not_distinct` | A trait has fewer than two levels, or levels that differ only in case or separators. |
| `level_alias_target_not_a_level` | A trait's level alias names a level that trait lacks. |
| `level_alias_is_already_a_level` | A trait alias gives a level a different meaning. |
| `level_alias_takes_a_level` | A list-wide alias is spelled like a trait's level, and that trait does not keep it. |

Shape errors come first. The checks across traits and aliases run once every value has a valid shape, so a list can show its problems in two rounds.

## Editing the list

- **Add a trait.** Give it a code, a name, a definition that stands alone, a domain and an identifier whose prefix is declared. For a numeric trait, choose the trait unit and list the printed units with exact factors.
- **Add a unit to a trait.** Add it to `printed_units` with its factor. Add it to `approximate_units` if the factor is a convention.
- **Add a spelling.** Use `unit_aliases` at the top when any trait could meet it, and on the trait when only one does. Check that the spelling does not mean something else in another unit.
- **Add a level.** Add it to `levels`.
- **Remove a trait.** Delete it from the file.

After an edit, run the tests and `list_problems` on the file.
