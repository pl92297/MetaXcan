import logging
import pandas
import numpy

from patsy import dmatrices
import statsmodels.api as sm

from MultiPrediXcanAssociation import Context as _MTPContext, MTPMode
from PrediXcanAssociation import Context as PContext, PMode
from .. expression import HDF5Expression, PlainTextExpression
from .. import Exceptions

########################################################################################################################
class MTPContext(_MTPContext):
    def __init__(self, args, expression):
        self.args = args
        self.mode = None
        self.covariates = None
        self.pheno = None
        self.pc_filter = _filter_from_args(args)
        self.expression = expression

    def get_genes(self):
        return self.expression.get_genes()

    def expression_for_gene(self, gene):
        return self.expression.expression_for_gene(gene)

    def get_pheno(self):
        return self.pheno

    def get_mode(self):
        return self.mode

    def get_covariates(self):
        return self.covariates

    def get_pc_filter(self):
        return self.pc_filter

    def __enter__(self):
        logging.info("Entering Multi-tissue PrediXcan context")
        self.expression.enter()
        _prepare_phenotype(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.info("Exiting Multi-tissue PrediXcan context")
        self.expression.exit()

def _check_args(args):
    if not args.mode in MTPMode.K_MODES:
        raise Exceptions.InvalidArguments("Invalid mode")

def _expression_from_args(args):
    if args.hdf5_expression_folder:
        logging.info("Preparing expression from HDF5 files")
        expression = HDF5Expression.ExpressionManager(args.hdf5_expression_folder, args.expression_pattern)
    elif args.memory_efficient and args.expression_folder:
        logging.info("Loading expression manager from text files (memory efficient)")
        expression = PlainTextExpression.ExpressionManagerMemoryEfficient(args.expression_folder, args.expression_pattern)
    elif args.expression_folder:
        logging.info("Loading expression manager from text files")
        expression = PlainTextExpression.ExpressionManager(args.expression_folder, args.expression_pattern)
    else:
        raise RuntimeError("Could not build expression from context")
    return expression

def mp_context_from_args(args):
    logging.info("Preparing Multi-Tissue PrediXcan context")
    _check_args(args)
    expression = _expression_from_args(args)
    context = MTPContext(args, expression)
    return context

def _filter_eigen_values_from_max(s, ratio):
    s_max = numpy.max(s)
    return [i for i,x in enumerate(s) if x > s_max*ratio]

def _filter_from_args(args):
    if args.pc_condition_number:
        return lambda x:_filter_eigen_values_from_max(x, 1.0/args.pc_condition_number)
    elif args.pc_eigen_ratio:
        return lambda x:_filter_eigen_values_from_max(x, args.pc_eigen_ratio)
    return None

########################################################################################################################

class _PContext(PContext):
    def __init__(self, args):
        self.args = args
        self.mode = None
        self.covariates = None
        self.pheno = None
        self.expression = None

    def get_genes(self):
        return self.expression.get_genes()

    def expression_for_gene(self, gene):
        return self.expression.expression_for_gene(gene)

    def get_pheno(self):
        return self.pheno

    def get_mode(self):
        return self.mode

    def get_covariates(self):
        return self.covariates

class HDF5PContext(_PContext):
    def __init__(self, args):
        super(HDF5PContext, self).__init__(args)
        self.h5 = None

    def __enter__(self):
        genes, h5 = HDF5Expression._structure_file(self.args.hdf5_expression_file)
        self.h5 = h5
        self.expression = HDF5Expression.Expression(genes, h5)
        _prepare_phenotype(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        HDF5Expression._close_file(self.h5)

class PlainTextPContext(_PContext):
    def __init__(self, args):
        super(PlainTextPContext, self).__init__(args)

    def __enter__(self):
        self.expression = PlainTextExpression.Expression(self.args.expression_file)
        _prepare_phenotype(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

def _check_args_file(args):
    if not args.mode in PMode.K_MODES:
        raise Exceptions.InvalidArguments("Invalid mode")

def p_context_from_args(args):
    _check_args_file(args)
    if args.hdf5_expression_file:
        logging.info("Preparing PrediXcan HDF5 context")
        context = HDF5PContext(args)
    elif args.expression_file:
        logging.info("Preparing PrediXcan context")
        context = PlainTextPContext(args)

    return context

########################################################################################################################

def _prepare_phenotype(context):
    logging.info("Accquiring phenotype")
    context.pheno = _pheno_from_file_and_column(context.args.input_phenos_file, context.args.input_phenos_column)
    if context.args.mode == MTPMode.K_LOGISTIC:
        v = set([str(float(x)) for x in context.pheno])
        if v != {'0.0', '1.0', 'nan'}:
            raise Exceptions.InvalidArguments("Logistic regression was asked but phenotype is not binomial")

    context.mode = context.args.mode
    if context.args.covariates_file and context.args.covariates:
        context.mode = MTPMode.K_LINEAR
        logging.info("Acquiring covariates")
        context.covariates = _get_covariates(context.args)
        logging.info("Replacing phenotype with residuals")
        context.pheno = _get_residual(context.pheno, context.covariates)

def _pheno_from_file_and_column(path, column):
    x = pandas.read_table(path, usecols=[column], sep="\s+")
    p = x[column]
    p.loc[numpy.isclose(p, -999.0, atol=1e-3, rtol=0)] = numpy.nan
    p = p.values
    return p

def _get_covariates(args):
    covariates = pandas.read_table(args.covariates_file, sep="\s+")[args.covariates]
    columns = covariates.columns.values
    for c in columns:
        covariates.loc[ numpy.isclose(covariates[c], -999.0, atol=1e-3, rtol=0), c] = numpy.nan
    return covariates

def _get_residual(pheno, covariates):
    e = pandas.DataFrame(covariates)
    e["order"] = xrange(0, e.shape[0])
    e["pheno"] = pheno

    e_ = e.dropna()
    e_ = e_[e_.columns[~(e_ == 0).all()]]

    keys = list(e_.columns.values)
    keys.remove("pheno")
    keys.remove("order")

    y, X = dmatrices("pheno ~ {}".format(" + ".join(keys)), data=e_, return_type="dataframe")
    model = sm.OLS(y,X)
    result = model.fit()
    e_["residual"] = result.resid
    e = e.merge(e_[["order", "residual"]], on="order", how="left")
    return e["residual"]