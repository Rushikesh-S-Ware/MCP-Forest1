"""
Main pipeline orchestrator for Nexus data processing.
Coordinates the entire ETL process from Excel to SQLite.
"""
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional
import argparse
import json
import shutil
from datetime import datetime

from nexus.config.settings import settings
from nexus.config.constants import EXPECTED_ROWS
from nexus.data.pipeline.loaders import ExcelLoader, DataValidator
from nexus.data.pipeline.transformers import (
    TreeCoverTransformer,
    PrimaryForestTransformer,
    CarbonTransformer,
    SubnationalTreeCoverTransformer,
    SubnationalPrimaryForestTransformer,
    SubnationalCarbonTransformer,
)
from nexus.data.pipeline.cleaners import DataCleaner
from nexus.data.database.exporter import DatabaseExporter
from nexus.data.database.schema import SchemaManager

from nexus.data.pipeline.pipeline_manager import PipelineManager
from nexus.data.metadata.metadata_manager import metadata_manager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NexusPipeline:
    """Main pipeline for processing Global Forest Watch data."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the pipeline.
        
        Args:
            config: Optional configuration overrides
        """
        self.config = config or {}
        self.stats = {}
        self.start_time = None
        


    def run(self, 
            input_file: Optional[Path] = None,
            output_db: Optional[Path] = None,
            drop_existing: bool = False,
            validate_only: bool = False) -> Dict[str, Any]:
        """
        Run the complete data processing pipeline with backup support.
        
        Args:
            input_file: Path to input Excel file
            output_db: Path to output SQLite database
            drop_existing: Whether to drop existing database tables
            validate_only: Only validate data without processing
            
        Returns:
            Dictionary with processing statistics
        """
        self.start_time = time.time()
        logger.info("="*60)
        logger.info("Starting Nexus Pipeline")
        logger.info("="*60)
        
        # Set database path
        db_path = output_db or settings.sqlite_db_path
        
        # BACKUP LOGIC: Create backup if database exists and we're not dropping
        if db_path.exists() and not drop_existing:
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            backup_name = f"{db_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db.backup"
            backup_path = backup_dir / backup_name
            
            logger.info(f"Creating backup: {backup_path}")
            shutil.copy2(db_path, backup_path)
            
            # Clean up old backups - THIS IS WHERE _cleanup_old_backups IS CALLED
            self._cleanup_old_backups(backup_dir, keep=5)
        
        # Initialize pipeline manager for transactional processing
        pipeline_mgr = PipelineManager()
        session_id = pipeline_mgr.start_session()
        
        try:
            # STEP 1: Load data with transaction
            with pipeline_mgr.transaction("load_data"):
                logger.info("Step 1: Loading Excel data")
                excel_path = input_file or settings.raw_data_path / settings.excel_file
                
                if not excel_path.exists():
                    raise FileNotFoundError(f"Excel file not found: {excel_path}")
                    
                loader = ExcelLoader(excel_path)
                
                tree_cover_df = loader.load_tree_cover_loss()
                primary_forest_df = loader.load_primary_forest()
                carbon_df = loader.load_carbon_data()
                
                self.stats["load_time"] = time.time() - self.start_time
                logger.info(f"Data loaded in {self.stats['load_time']:.2f} seconds")
                logger.info(f"  - Tree cover: {len(tree_cover_df)} rows")
                logger.info(f"  - Primary forest: {len(primary_forest_df)} rows")
                logger.info(f"  - Carbon: {len(carbon_df)} rows")

                # Subnational (state/province) sheets - loaded only if present
                self.has_subnational = loader.has_subnational_sheets()
                sub_tree_cover_df = sub_primary_forest_df = sub_carbon_df = None
                if self.has_subnational:
                    sub_tree_cover_df = loader.load_subnational_tree_cover_loss()
                    sub_primary_forest_df = loader.load_subnational_primary_forest()
                    sub_carbon_df = loader.load_subnational_carbon_data()
                    logger.info(f"  - Subnational tree cover: {len(sub_tree_cover_df)} rows")
                    logger.info(f"  - Subnational primary forest: {len(sub_primary_forest_df)} rows")
                    logger.info(f"  - Subnational carbon: {len(sub_carbon_df)} rows")
                else:
                    logger.warning("Subnational sheets not found; skipping subnational tables.")
            
            # STEP 2: Validate data with transaction
            with pipeline_mgr.transaction("validate_data"):
                logger.info("Step 2: Validating data")
                validation_results = self._validate_data(
                    tree_cover_df, primary_forest_df, carbon_df
                )
                
                if validate_only:
                    logger.info("Validation-only mode. Stopping pipeline.")
                    pipeline_mgr.complete_session()
                    return validation_results
                    
                if not validation_results["valid"]:
                    logger.warning(f"Data validation has warnings: {validation_results['warnings']}")
                    # Continue anyway for now
            
            # STEP 3: Clean data with transaction
            with pipeline_mgr.transaction("clean_data"):
                logger.info("Step 3: Cleaning data")
                cleaner = DataCleaner()
                
                tree_cover_df = cleaner.clean_country_names(tree_cover_df)
                primary_forest_df = cleaner.clean_country_names(primary_forest_df)
                carbon_df = cleaner.clean_country_names(carbon_df)
                
                # Fix negative values where inappropriate
                tree_cover_df = cleaner.fix_negative_values(
                    tree_cover_df, ["tree_cover_loss_ha", "extent_2000_ha"]
                )
                carbon_df = cleaner.fix_negative_values(
                    carbon_df, ["carbon_emissions_mg_co2e"]  # Note: net_flux can be negative
                )

                if self.has_subnational:
                    sub_tree_cover_df = cleaner.clean_country_names(sub_tree_cover_df)
                    sub_primary_forest_df = cleaner.clean_country_names(sub_primary_forest_df)
                    sub_carbon_df = cleaner.clean_country_names(sub_carbon_df)
            
            # STEP 4: Transform data with transaction
            with pipeline_mgr.transaction("transform_data"):
                logger.info("Step 4: Transforming data to three-table architecture")
                
                # Transform tree cover loss
                tree_transformer = TreeCoverTransformer()
                tree_cover_fact = tree_transformer.transform(tree_cover_df)
                self.stats["tree_cover_rows"] = len(tree_cover_fact)
                
                # Transform primary forest
                primary_transformer = PrimaryForestTransformer()
                primary_forest_fact = primary_transformer.transform(primary_forest_df)
                self.stats["primary_forest_rows"] = len(primary_forest_fact)
                
                # Transform carbon data
                carbon_transformer = CarbonTransformer()
                carbon_fact = carbon_transformer.transform(carbon_df)
                self.stats["carbon_rows"] = len(carbon_fact)

                # Subnational transforms
                sub_tree_cover_fact = sub_primary_forest_fact = sub_carbon_fact = None
                if self.has_subnational:
                    sub_tree_cover_fact = SubnationalTreeCoverTransformer().transform(sub_tree_cover_df)
                    sub_primary_forest_fact = SubnationalPrimaryForestTransformer().transform(sub_primary_forest_df)
                    sub_carbon_fact = SubnationalCarbonTransformer().transform(sub_carbon_df)
                    self.stats["sub_tree_cover_rows"] = len(sub_tree_cover_fact)
                    self.stats["sub_primary_forest_rows"] = len(sub_primary_forest_fact)
                    self.stats["sub_carbon_rows"] = len(sub_carbon_fact)
                
                logger.info(f"Transformation complete:")
                logger.info(f"  - Tree cover: {self.stats['tree_cover_rows']:,} rows")
                logger.info(f"  - Primary forest: {self.stats['primary_forest_rows']:,} rows")
                logger.info(f"  - Carbon: {self.stats['carbon_rows']:,} rows")

                # Data check before export
                logger.info("Data check before export:")
                logger.info(f"  Tree cover shape: {tree_cover_fact.shape}")
                logger.info(f"  Primary forest shape: {primary_forest_fact.shape}")
                logger.info(f"  Carbon shape: {carbon_fact.shape}")
            
            # STEP 5: Export to database with transaction
            with pipeline_mgr.transaction("export_data"):
                logger.info("Step 5: Exporting to SQLite database")
                
                # Ensure directory exists
                db_path.parent.mkdir(parents=True, exist_ok=True)
                
                exporter = DatabaseExporter(db_path)
                exporter.initialize_database(drop_existing=drop_existing)
                
                export_results = exporter.export_all_tables(
                    tree_cover_df=tree_cover_fact,
                    primary_forest_df=primary_forest_fact,
                    carbon_df=carbon_fact
                )

                # Export subnational fact tables (created by initialize_database)
                if self.has_subnational:
                    export_results.update(exporter.export_subnational_tables(
                        subnational_tree_cover_df=sub_tree_cover_fact,
                        subnational_primary_forest_df=sub_primary_forest_fact,
                        subnational_carbon_df=sub_carbon_fact,
                    ))
                
                self.stats["export_results"] = export_results
                logger.info(f"Database created at: {db_path}")
            
            # STEP 6: Create dimension tables with transaction
            with pipeline_mgr.transaction("create_dimensions"):
                logger.info("Step 6: Creating dimension tables")
                dimensions = exporter.create_dimension_tables()
                self.stats["dimension_tables"] = {
                    name: len(df) for name, df in dimensions.items()
                }
            
            # STEP 7: Validate export with transaction
            with pipeline_mgr.transaction("validate_export"):
                logger.info("Step 7: Validating exported data")
                validation = exporter.validate_export()
                self.stats["export_validation"] = validation
            
            # Calculate total time
            self.stats["total_time"] = time.time() - self.start_time

            # STEP 8: Update centralized metadata (no longer generating/saving separately)
            logger.info("Step 8: Updating centralized metadata")
            metadata_manager.update_runtime_stats({
                "row_counts": self.stats["export_results"],
                "validation_status": {
                    table: val.get("validation", "UNKNOWN") 
                    for table, val in validation.items()
                },
                "data_quality": {
                    "completeness": validation_results.get("completeness", {}).get("tree_cover", 0),
                    "accuracy": 1.0,  # You can calculate this based on your criteria
                    "consistency": 1.0
                },
                "pipeline_run": True,
                "version": "1.0.0"
            })
            
            # Mark pipeline session as complete
            pipeline_mgr.complete_session()
            
            # Final summary
            self._print_summary()
            
            return self.stats
            
        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}")
            self.stats["error"] = str(e)
            self.stats["total_time"] = time.time() - self.start_time if self.start_time else 0
            # Pipeline manager handles rollback automatically in transaction context
            raise

    def _cleanup_old_backups(self, backup_dir: Path, keep: int = 5):
        """
        Keep only the most recent backups.
        Called by run() method after creating a new backup.
        
        Args:
            backup_dir: Directory containing backups
            keep: Number of backups to keep
        """
        try:
            # Get all backup files sorted by modification time
            backups = sorted(
                backup_dir.glob("*.db.backup"), 
                key=lambda x: x.stat().st_mtime
            )
            
            # If we have more than 'keep' backups, remove the oldest ones
            if len(backups) > keep:
                for old_backup in backups[:-keep]:  # Keep the last 'keep' files
                    logger.info(f"Removing old backup: {old_backup.name}")
                    old_backup.unlink()
                logger.info(f"Kept {keep} most recent backups")
        except Exception as e:
            logger.warning(f"Failed to cleanup old backups: {e}")
            # Don't fail the pipeline just because cleanup failed
            
            
    def _validate_data(self, tree_cover_df, primary_forest_df, carbon_df) -> Dict[str, Any]:
        """Validate loaded data."""
        validator = DataValidator()
        results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "completeness": {}
        }
        
        # Check completeness
        for name, df in [
            ("tree_cover", tree_cover_df),
            ("primary_forest", primary_forest_df),
            ("carbon", carbon_df)
        ]:
            completeness = validator.check_data_completeness(df)
            results["completeness"][name] = completeness
            
            if completeness < settings.min_completeness_score:
                results["warnings"].append(
                    f"{name} completeness {completeness:.1%} below threshold"
                )
                
        # Validate year columns
        if not validator.validate_year_columns(tree_cover_df, 2001, 2024):
            results["warnings"].append("Tree cover missing some year columns")
            
        if not validator.validate_year_columns(primary_forest_df, 2002, 2023):
            results["warnings"].append("Primary forest missing some year columns")
            
        return results
        
        
    def _print_summary(self):
        """Print pipeline summary."""
        logger.info("="*60)
        logger.info("Pipeline Complete!")
        logger.info("="*60)
        logger.info(f"Total time: {self.stats['total_time']:.2f} seconds")
        logger.info(f"Database location: {settings.sqlite_db_path}")
        logger.info("")
        logger.info("Table row counts:")
        for table, count in self.stats["export_results"].items():
            logger.info(f"  - {table}: {count:,} rows")
        logger.info("")
        logger.info("Validation results:")
        for table, validation in self.stats["export_validation"].items():
            status = validation.get("validation", "UNKNOWN")
            logger.info(f"  - {table}: {status}")
        logger.info("="*60)


def main():
    """Main entry point for the pipeline."""
    parser = argparse.ArgumentParser(description="Nexus Data Processing Pipeline")
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to input Excel file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Path to output SQLite database"
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing database tables"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate data without processing"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        
    # Run pipeline
    pipeline = NexusPipeline()
    
    try:
        stats = pipeline.run(
            input_file=args.input,
            output_db=args.output,
            drop_existing=args.drop_existing,
            validate_only=args.validate_only
        )
        
        # Exit with success
        return 0
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())