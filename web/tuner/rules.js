// The page's copies of names the server owns.
//: TagTuner's lists of faces that are not a person's, as the server names them
//: (tagpup.core.vocabulary.BUCKETS; tests/test_bucket_names_have_one_owner.py holds
//: this copy to it). Nobody can be called one of them (#68).
//: The grids' tabs for each band the server names (tagpup.core.clustering.band).
export const BAND_OF = Object.freeze({likely: 'high', possible: 'lower'});
export const BUCKET = Object.freeze({UNKNOWN: 'Unknown Faces', UNGROUPED: 'Ungrouped', EXCLUDED: 'Excluded'});
export const isBucket = name => Object.values(BUCKET).includes(name);

//: What a face or photo whose year is not known is grouped under, as the server
//: sends it (tagpup.core.dates.UNKNOWN_YEAR; tests/test_rules_have_one_owner.py
//: holds this copy to it).
export const UNKNOWN_YEAR = 'Unknown';
