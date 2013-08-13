import datetime
import hammer

from colander import (
    MappingSchema,
    SchemaNode,
    Bool,
    Int,
    Float,
    Range,
    String,
    SequenceSchema,
    OneOf,
    TupleSchema,
    deferred,
    drop,
)
from gnome.persist import (
    environment_schema,
    movers_schema,
    map_schema,
    spills_schema,
    validators,
    extend_colander)
from gnome.persist.extend_colander import LocalDateTime
from gnome.persist.base_schema import LongLatBounds
from gnome.persist.movers_schema import CatsMover
from gnome.persist.movers_schema import GridCurrentMover


@deferred
def now(node, kw):
    return datetime.datetime.now()


class LongLat(MappingSchema):
    """
    A :class:`colander.MappingSchema`-based LongLat schema, as opposed to the
    TupleSchema-based class:`gnome.persist.base_schema.LongLat` version.
    """
    long = SchemaNode(Float())
    lat = SchemaNode(Float())


class WindMoverSchema(movers_schema.WindMover):
    default_name = 'Wind Mover'
    name = SchemaNode(String(), default=default_name, missing=default_name)
    uncertain_angle_scale_units = SchemaNode(String(), default='rad',
                                             missing='rad',
                                             validator=OneOf(['rad', 'deg']))


class RandomMoverSchema(movers_schema.RandomMover):
    default_name = 'Random Mover'
    name = SchemaNode(String(), default=default_name, missing=default_name)
    diffusion_coef = SchemaNode(Float(), default=100000, missing=100000)


class PositionSchema(TupleSchema):
    start_position_x = SchemaNode(Float())
    start_position_y = SchemaNode(Float())
    start_position_z = SchemaNode(Float())


class WindageRangeSchema(TupleSchema):
    windage_min = SchemaNode(Float())
    windage_max = SchemaNode(Float())


class PointSourceReleaseSchema(spills_schema.PointSourceRelease):
    default_name = 'Surface Release Spill'
    name = SchemaNode(String(), default=default_name, missing=default_name)


class PointSourceReleasesSchema(SequenceSchema):
    spill = PointSourceReleaseSchema()


class WindMoversSchema(SequenceSchema):
    mover = WindMoverSchema()


class RandomMoversSchema(SequenceSchema):
    mover = RandomMoverSchema()


class MapSchema(map_schema.MapFromBNA):
    default_name = 'Map'
    refloat_halflife = SchemaNode(Float(), default=6 * 3600)  # seconds
    name = SchemaNode(String(), default=default_name, missing=default_name)
    filename = SchemaNode(String(), default=None, missing=drop)
    map_bounds = LongLatBounds(default=[], missing=drop)
    background_image_url = SchemaNode(String(), default=None, missing=drop)


# Input values GOODS expects for the `resolution` field on a custom map form.
custom_map_resolutions = [
    'c',  # course
    'l',  # low
    'i',  # intermediate
    'h',  # high
    'f'   # full
]


class CustomMapSchema(MappingSchema):
    default_name = 'Map'
    name = SchemaNode(String(), default=default_name, missing=default_name)
    map_bounds = LongLatBounds(missing=drop)
    north_lat = SchemaNode(Float())
    west_lon = SchemaNode(Float())
    east_lon = SchemaNode(Float())
    south_lat = SchemaNode(Float())
    cross_dateline = SchemaNode(Bool(), missing=False, default=False)
    resolution = SchemaNode(String(), validator=OneOf(custom_map_resolutions),
                            default='i', missing='i')
    refloat_halflife = SchemaNode(Float(), default=1)


class WindSchema(environment_schema.Wind):
    default_name = 'Wind'
    name = SchemaNode(String(), default=default_name, missing=default_name)


class WindsSchema(SequenceSchema):
    wind = WindSchema()


class WebCatsMover(CatsMover):
    default_name = 'Cats Mover'
    name = SchemaNode(String(), default=default_name, missing=default_name)


class CatsMoversSchema(SequenceSchema):
    mover = WebCatsMover()

class WebGridCurrentMover(GridCurrentMover):
    default_name = 'Grid Current Mover'
    name = SchemaNode(String(), default=default_name, missing=default_name)


class GridCurrentMoversSchema(SequenceSchema):
    mover = WebGridCurrentMover()


class ModelSchema(MappingSchema):
    id = SchemaNode(String(), missing=drop)
    start_time = SchemaNode(LocalDateTime(), default=now,
                            validator=validators.convertible_to_seconds)
    duration_days = SchemaNode(Int(), default=1, validator=Range(min=0))
    duration_hours = SchemaNode(Int(), default=0, validator=Range(min=0))
    uncertain = SchemaNode(Bool(), default=False)
    time_step = SchemaNode(Float(), default=0.1)
    surface_release_spills = PointSourceReleasesSchema(
        default=[], missing=drop)
    wind_movers = WindMoversSchema(default=[], missing=drop)
    random_movers = RandomMoversSchema(default=[], missing=drop)
    cats_movers = CatsMoversSchema(default=[], missing=drop)
    grid_current_movers = GridCurrentMoversSchema(default=[], missing=drop)
    winds = WindsSchema(default=[], missing=drop)
    map = MapSchema(missing=drop)


class LocationFileSchema(MappingSchema):
    name = SchemaNode(String())
    latitude = SchemaNode(Float())
    longitude = SchemaNode(Float())
    model_data = ModelSchema()


@hammer.adapts(validators.positive)
def adapt_positive(schema, **kwargs):
    return {
        'minimum': 0
    }


hammer.register_adapter(extend_colander.LocalDateTime, hammer.adapt_datetime)
hammer.register_adapter([extend_colander.DatetimeValue2dArray,
                         extend_colander.DatetimeValue2dArraySchema],
                        hammer.adapt_sequence)
hammer.register_adapter([extend_colander.DefaultTupleSchema,
                         extend_colander.DefaultTuple], hammer.adapt_tuple)
